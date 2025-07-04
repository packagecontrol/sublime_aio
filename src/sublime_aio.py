from __future__ import annotations

import asyncio
import atexit
import traceback
from inspect import iscoroutinefunction
from threading import Lock, Thread
from time import monotonic as now
from typing import TYPE_CHECKING, overload

import sublime
import sublime_api
import sublime_plugin

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from concurrent.futures import Future
    from typing import Any, Callable, List, Tuple, TypeVar, Union

    from typing_extensions import ParamSpec, TypeAlias

    P = ParamSpec('P')
    T = TypeVar('T')

    BlankCoro: TypeAlias = Coroutine[object, None, None]
    CompletionsReturnVal: TypeAlias = Union[
        sublime.CompletionList,
        Tuple[List[sublime.CompletionValue], sublime.AutoCompleteFlags],
        List[sublime.CompletionValue],
        None,
    ]

    EL = TypeVar('EL', bound='EventListener')
    VEL = TypeVar('VEL', bound='ViewEventListener')


__all__ = [
    # commands
    "ApplicationCommand",
    "WindowCommand",
    "ViewCommand",
    # listeners
    "EventListener",
    "ViewEventListener",
    # decorators
    "debounced",
    # functions
    "run_coroutine",
]

__version__ = "0.1.5"

# ---- [ internal ] -----------------------------------------------------------

_loop: asyncio.AbstractEventLoop | None = None
_thread: Thread | None = None

if _loop is None:
    _loop = asyncio.new_event_loop()
    _thread = Thread(target=_loop.run_forever)
    _thread.daemon = True
    _thread.start()


@atexit.register
def on_exit():
    global _loop
    if _loop is None or _thread is None:
        return

    loop = _loop
    _loop = None

    def shutdown():
        for task in asyncio.all_tasks(loop):
            task.cancel()
        loop.stop()

    loop.call_soon_threadsafe(shutdown)
    _thread.join()
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()


# ---- [ public ] -------------------------------------------------------------


# Using pyright's type inference to get this type right – for now.
def debounced(delay_in_ms: int):
    """Call coroutine as soon as no more events arrive within specified delay.

    Performs view-specific tracking and is best suited for the
    `on_modified` and `on_selection_modified` methods.
    The `view` is taken from the first argument for `EventListener`s
    and from the instance for `ViewEventListener`s.

    Calls are only made when the `view` is still "valid" according to ST's API,
    so it's not necessary to check it in the wrapped function.

    Examples:

    ```py
    class DebouncedListener(sublime_aio.EventListener):
        @sublime_aio.debounced(500)
        async def on_modified(self, view):
            print("debounced EventListener.on_modified", view.id())

    class DebouncedViewListener(sublime_aio.ViewEventListener):
        @sublime_aio.debounced(1000)
        async def on_modified(self):
            print("debounced ViewEventListener.on_modified_async", self.view.id())
    ```
    """
    @overload
    def decorator(coro_func: Callable[[EL, sublime.View], BlankCoro]) -> Callable[[EL, sublime.View], None]: ...

    @overload
    def decorator(coro_func: Callable[[VEL], BlankCoro]) -> Callable[[VEL], None]: ...

    def decorator(
        coro_func: Callable[[EL, sublime.View], BlankCoro] | Callable[[VEL], BlankCoro],
    ) -> Callable[..., None]:
        # Maps a view id to a timestamp of a monotic clock in fractional seconds.
        call_at: dict[int, float] = {}
        lock = Lock()

        async def debounce(
            view: sublime.View,
            coro_func: Callable,
            self: EventListener | ViewEventListener,
            *args
        ):
            """
            Coroutine scheduling delayed event handler coroutine execution.

            Event handler is executed if no further event was received within specified delay.

            :param view:
                The view handling the event for.
            :param coro_func:
                The coroutine function (event listener method) to schedule.
            :param self:
                The event listener instance for which to schedule event handler.
            :param args:
                The arguments passed to coroutine function by ST API.
            """
            while True:
                with lock:
                    time_to_wait = delay_in_ms / 1000 + call_at[view.view_id] - now()
                    if time_to_wait <= 0:
                        del call_at[view.view_id]
                        break
                
                await asyncio.sleep(time_to_wait)

            if view.is_valid():
                await coro_func(self, *args)

        def wrapper(self: EventListener | ViewEventListener, *args: sublime.View) -> None:
            """
            Wrapper function called on UI thread to schedule debounced coroutine execution

            :param self:
                The event listener instance for which to schedule event handler.
            :param args:
                The arguments passed to coroutine function by ST API.
            """
            if _loop is None:
                return

            view = self.view if isinstance(self, ViewEventListener) else args[0]

            with lock:
                if view.view_id in call_at:
                    call_at[view.view_id] = now()
                    return
                call_at[view.view_id] = now()

            asyncio.run_coroutine_threadsafe(debounce(view, coro_func, self, *args), loop=_loop)

        return wrapper

    return decorator


def run_coroutine(coro: Coroutine[object, object, T]) -> Future[T]:
    """
    Run coroutine from synchronous code.

    Example:

    ```py
    import sublime_aio

    async def an_async_func(arg1, arg2):
        ...

    def sync_func(arg1, arg2):

        def on_done(future):
            ...

        future = sublime_aio.run_coroutine(an_async_func(arg1, arg2))
        future.add_done_callback(on_done)
    ```

    :param coro:
        The coroutine object to run

    :returns:
        An `concurrent.Future` object
    """
    if _loop is None:
        raise RuntimeError("No event loop running!")

    return asyncio.run_coroutine_threadsafe(coro, loop=_loop)


def active_window() -> Window:
    """
    :returns: The most recently used `Window`.
    """
    return Window(sublime_api.active_window())


def windows() -> list[Window]:
    """
    :returns: A list of all the open windows.
    """
    return [Window(id) for id in sublime_api.windows()]


class ApplicationCommand(sublime_plugin.ApplicationCommand):
    """
    An async `Command` instantiated just once.
    """

    def run_(self, edit_token: int, args: dict[str, Any]) -> None:
        args = self.filter_args(args)
        try:
            run_coroutine(self.run(**args) if args else self.run())
        except TypeError as e:
            if 'required positional argument' in str(e):
                if sublime_api.can_accept_input(self.name(), args):
                    sublime.active_window().run_command(
                        'show_overlay',
                        {
                            'overlay': 'command_palette',
                            'command': self.name(),
                            'args': args
                        }
                    )
                    return
            raise

    async def run(self) -> None:
        """
        Called when the command is run. Command arguments are passed as keyword
        arguments.
        """
        raise NotImplementedError


class WindowCommand(sublime_plugin.WindowCommand):
    """
    An async `Command` instantiated once per window. The `Window` object may be
    retrieved via `self.window <window>`.
    """

    def __init__(self, window: sublime.Window):
        """:meta private:"""

        self.window: Window = Window(window.id())
        """ The asyncio supporting `Window` this command is attached to. """

    def run_(self, edit_token: int, args: dict[str, Any]) -> None:
        args = self.filter_args(args)
        try:
            run_coroutine(self.run(**args) if args else self.run())
        except TypeError as e:
            if 'required positional argument' in str(e):
                if sublime_api.window_can_accept_input(self.window.id(), self.name(), args):
                    sublime_api.window_run_command(
                        self.window.id(),
                        'show_overlay',
                        {
                            'overlay': 'command_palette',
                            'command': self.name(),
                            'args': args
                        }
                    )
                    return
            raise

    async def run(self) -> None:
        """
        Called when the command is run. Command arguments are passed as keyword
        arguments.
        """
        raise NotImplementedError


class ViewCommand(sublime_plugin.TextCommand):
    """
    An async `Command` instantiated once per `View`. The `View` object may be
    retrieved via `self.view <view>`.

    It is like a `TextCommand` but doesn't provide an `edit` token, because
    it wouldn't be valid anymore, when `async def run()` is invoked.

    Example:

    ```py
    class MyViewCommand(sublime_aio.ViewCommand):
        async def run(self):
            self.view.close()
    ```
    """

    def run_(self, edit_token: int, args: dict[str, Any]) -> None:
        args = self.filter_args(args)
        try:
            run_coroutine(self.run(**args) if args else self.run())
        except TypeError as e:
            if 'required positional argument' in str(e):
                if sublime_api.view_can_accept_input(self.view.id(), self.name(), args):
                    sublime_api.window_run_command(
                        sublime_api.view_window(self.view.id()),
                        'show_overlay',
                        {
                            'overlay': 'command_palette',
                            'command': self.name(),
                            'args': args
                        }
                    )
                    return
            raise

    async def run(self) -> None:
        """
        Called when the command is run. Command arguments are passed as keyword
        arguments.
        """
        raise NotImplementedError


class AsyncEventListenerType(type):
    """
    This class describes an asynchronous event listener meta class.

    It wraps all coroutines which start with `on_` into synchronous methods
    for ST to execute them. Wrapper methods schedule execution of coroutines
    in global event loop.

    A `sublime.CompletionList()` is created and returned before async
    `on_query_completions` is scheduled for execution.
    ```
    """

    def __new__(
        mcs: type[AsyncEventListenerType],
        name: str,
        bases: tuple[type, ...],
        attrs: dict[str, object],
    ) -> AsyncEventListenerType:
        for attr_name, attr_value in attrs.items():
            # wrap `async def on_query_completions()` in sync method of same name
            if attr_name == 'on_query_completions' and iscoroutinefunction(attr_value):
                _task = None
                completions_coro_func: Callable[..., Coroutine[object, object, CompletionsReturnVal]] = attr_value

                async def query_completions(
                    clist: sublime.CompletionList,
                    coro: Coroutine[object, object, CompletionsReturnVal],
                ) -> None:
                    try:
                        completions = await coro
                        if isinstance(completions, sublime.CompletionList):
                            clist.set_completions(completions.completions or [], completions.flags)
                        elif isinstance(completions, tuple):
                            clist.set_completions(completions[0], completions[1])
                        else:
                            clist.set_completions(completions or [])
                    except asyncio.CancelledError:
                        clist.set_completions([])
                    except BaseException:
                        clist.set_completions([])
                        traceback.print_exc()

                def on_query_completions(*args: P.args, **kwargs: P.kwargs) -> sublime.CompletionList:
                    nonlocal _task

                    if _task:
                        _task.cancel()

                    clist = sublime.CompletionList()
                    _task = run_coroutine(query_completions(clist, completions_coro_func(*args, **kwargs)))
                    return clist

                attrs[attr_name] = on_query_completions

            # wrap `async def on_...()` in sync method of same name
            elif attr_name in sublime_plugin.all_callbacks and iscoroutinefunction(attr_value):
                if attr_name.endswith('_async'):
                    raise ValueError('Invalid event handler name! Coroutines must not end with "_async"!')
                coro_func: Callable[..., Coroutine[object, object, None]] = attr_value

                def on_event(*args: P.args, **kwargs: P.kwargs) -> None:
                    run_coroutine(coro_func(*args, **kwargs))

                attrs[attr_name] = on_event

        return super().__new__(mcs, name, bases, attrs)


class EventListener(sublime_plugin.EventListener, metaclass=AsyncEventListenerType):
    """
    This class describes an asyncio event listener.

    It extends `sublime_plugin.EventListener` to support event handler coroutines
    which behave the same way as default methods.

    Example:

    ```py
    class MyEventListener(sublime_aio.EventListener):
        async def on_modified(self, view):
            ...

        async def on_query_completions(self, view):
            # note: CompletionLists must return in resolved state!
            return sublime.CompletionList(["item1", "item2"])
    """
    pass


class ViewEventListener(sublime_plugin.ViewEventListener, metaclass=AsyncEventListenerType):
    """
    This class describes an asyncio view event listener.

    It extends `sublime_plugin.ViewEventListener` to support event handler coroutines
    which behave the same way as default methods.

    Example:

    ```py
    class MyEventListener(sublime_aio.ViewEventListener):
        async def on_modified(self):
            ...

        async def on_query_completions(self):
            # note: CompletionLists must return in resolved state!
            return sublime.CompletionList(["item1", "item2"])
    """
    pass


class AsyncTextChangeListenerType(type):
    """
    This class describes an asynchronous text change listener meta class.

    It wraps all coroutines which start with `on_` into synchronous methods
    for ST to execute them. Wrapper methods schedule execution of coroutines
    in global event loop.
    """

    def __new__(
        mcs: type[AsyncTextChangeListenerType],
        name: str,
        bases: tuple[type, ...],
        attrs: dict[str, object],
    ) -> AsyncTextChangeListenerType:
        for attr_name, attr_value in attrs.items():
            # wrap `async def on_...()` in sync method of same name
            if attr_name in sublime_plugin.text_change_listener_callbacks and iscoroutinefunction(attr_value):
                if attr_name.endswith('_async'):
                    raise ValueError('Invalid event handler name! Coroutines must not end with "_async"!')

                # note: `coro_func` must be part of on_event() signature to
                #       create unique function object as otherwise all events
                #       call last `on_...` coroutine defined by listener.
                #       Handler is not called, when using `partial()`!
                #       It's actually unclear, why it is working without in
                #       `AsyncEventListenerType`.
                def on_event(
                    *args: P.args,
                    coro_func: Callable[..., Coroutine[object, object, None]] = attr_value,  # pyright: ignore
                    **kwargs: P.kwargs,
                ) -> None:
                    run_coroutine(coro_func(*args, **kwargs))

                attrs[attr_name] = on_event

        return super().__new__(mcs, name, bases, attrs)


class TextChangeListener(sublime_plugin.TextChangeListener, metaclass=AsyncTextChangeListenerType):
    """
    A class that provides event handling about text changes made to a specific
    Buffer. Is separate from `ViewEventListener` since multiple views can
    share a single buffer.

    It extends `sublime_plugin.TextChangeListener` to support event handler coroutines
    which behave the same way as default methods.

    .. since:: 4081

    .. method:: on_text_changed(changes: List[TextChange])

        Called once after changes has been made to a buffer, with detailed
        information about what has changed.

    .. method:: on_revert()

        Called when the buffer is reverted.

        A revert does not trigger text changes. If the contents of the buffer
        are required here use `View.substr`.

    .. method:: on_reload()

        Called when the buffer is reloaded.

        A reload does not trigger text changes. If the contents of the buffer
        are required here use `View.substr`.
    """
    pass


class InputCancelledError(Exception):
    """
    This class describes an input cancelled error.

    It is raised whenever input panels or quick panels are closed via escape key.
    """
    pass


class Window(sublime.Window):
    """
    This class describes an extended `sublime.Window`.

    It overrides some methods with coroutines.
    """

    async def show_input_panel(
        self,
        caption: str,
        initial_text: str = "",
        on_change: Callable[[sublime.View, str], Coroutine[object, object, T]] | None = None,
    ) -> str:
        view = None
        fut = asyncio.Future()

        def cancel() -> None:
            if _loop:
                _loop.call_soon_threadsafe(fut.set_exception, InputCancelledError)

        def done(text: str) -> None:
            if _loop:
                _loop.call_soon_threadsafe(fut.set_result, text)

        def change(text: str) -> None:
            if view is not None:
                run_coroutine(on_change(view, text))

        view = super().show_input_panel(
            caption=caption,
            initial_text=initial_text,
            on_done=done,
            on_change=change if on_change else None,
            on_cancel=cancel,
        )

        return await fut

    async def show_quick_panel(
        self,
        items: list[str] | list[list[str]] | list[sublime.QuickPanelItem],
        flags: sublime.QuickPanelFlags = sublime.QuickPanelFlags.NONE,
        selected_index: int = -1,
        on_highlight: Callable[[int], Coroutine[object, object, T]] | None = None,
        placeholder: str | None = None,
    ) -> int:
        fut = asyncio.Future()

        def highlight(index):
            run_coroutine(on_highlight(index))

        def select(index):
            if _loop:
                if index == -1:
                    _loop.call_soon_threadsafe(fut.set_exception, InputCancelledError)
                else:
                    _loop.call_soon_threadsafe(fut.set_result, index)

        super().show_quick_panel(
            items=items,
            flags=flags,
            selected_index=1,
            placeholder=placeholder,
            on_highlight=highlight if on_highlight else None,
            on_select=select,
        )

        return await fut
