# encoding: utf-8
"""
bridge.py — Thread-safe main-thread dispatcher for GlyphsApp API calls.

The Problem:
  HTTP server runs on a background thread. GlyphsApp API objects (GSFont, GSPath, etc.)
  are Objective-C objects via PyObjC — NOT thread-safe. Touching them off-main-thread = crash.

The Solution:
  HTTP thread puts work items in a Queue. An NSTimer on the main thread polls the queue
  every 50ms, executes the work, stores the result, and signals the HTTP thread.

See ARCHITECTURE.md §3.3 for the full rationale.

Usage from HTTP handler:
    result = bridge.execute_on_main(some_function, arg1, arg2)
    # Blocks until main thread executes and returns result
"""

import queue
import threading
import traceback

from Foundation import NSTimer, NSRunLoop, NSDefaultRunLoopMode
import objc


class WorkItem:
	"""A unit of work to be executed on the main thread."""

	__slots__ = ('func', 'args', 'kwargs', 'event', 'result', 'error')

	def __init__(self, func, args=(), kwargs=None):
		self.func = func
		self.args = args
		self.kwargs = kwargs or {}
		self.event = threading.Event()  # Signaled when work is done
		self.result = None
		self.error = None


class MainThreadBridge:
	"""Dispatches work from background threads to the main thread via Queue + NSTimer."""

	POLL_INTERVAL = 0.05  # 50ms = 20 checks/sec
	TIMEOUT = 10.0         # Max wait for main thread response

	def __init__(self):
		self._queue = queue.Queue()
		self._timer = None
		self._running = False

	@objc.python_method
	def start(self):
		"""Start the NSTimer that polls the queue on the main thread."""
		if self._running:
			return

		self._running = True

		# NSTimer.scheduledTimerWithTimeInterval... schedules on the CURRENT thread's run loop.
		# Since start() is called from the plugin's start() method, which runs on main thread,
		# this timer will fire on the main thread. Exactly what we need.
		self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
			self.POLL_INTERVAL,
			self,
			objc.selector(self.drainQueue_, signature=b'v@:@'),
			None,
			True
		)
		# Make sure timer fires even during modal dialogs and tracking
		NSRunLoop.currentRunLoop().addTimer_forMode_(self._timer, NSDefaultRunLoopMode)

	@objc.python_method
	def stop(self):
		"""Stop the timer and clear the queue."""
		self._running = False
		if self._timer:
			self._timer.invalidate()
			self._timer = None

		# Drain remaining items with errors
		while not self._queue.empty():
			try:
				item = self._queue.get_nowait()
				item.error = Exception("Bridge shutting down")
				item.event.set()
			except queue.Empty:
				break

	def drainQueue_(self, timer):
		"""NSTimer callback — runs on main thread. Drains and executes all pending work."""
		if not self._running:
			return

		# Process all pending items (not just one)
		items_processed = 0
		while not self._queue.empty() and items_processed < 10:  # Cap to avoid blocking UI
			try:
				item = self._queue.get_nowait()
			except queue.Empty:
				break

			try:
				item.result = item.func(*item.args, **item.kwargs)
			except Exception as e:
				item.error = e
				print(f"[GlyphsMCP Bridge] Error executing {item.func.__name__}: {e}")
				traceback.print_exc()

			item.event.set()  # Signal the waiting HTTP thread
			items_processed += 1

	@objc.python_method
	def execute_on_main(self, func, *args, **kwargs):
		"""Execute a function on the main thread and return its result.

		Called from HTTP thread. Blocks until main thread executes the function.

		Args:
			func: Callable to execute (will have access to Glyphs API)
			*args: Positional arguments
			**kwargs: Keyword arguments

		Returns:
			Whatever func returns

		Raises:
			TimeoutError: If main thread doesn't respond within TIMEOUT seconds
			Exception: Whatever func raised, re-raised in the calling thread
		"""
		if not self._running:
			raise RuntimeError("Bridge is not running")

		item = WorkItem(func, args, kwargs)
		self._queue.put(item)

		# Block until main thread signals completion
		signaled = item.event.wait(timeout=self.TIMEOUT)

		if not signaled:
			raise TimeoutError(
				f"Main thread did not respond within {self.TIMEOUT}s. "
				f"GlyphsApp may be busy (modal dialog, long operation)."
			)

		if item.error:
			raise item.error

		return item.result
