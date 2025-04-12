from __future__ import annotations

import asyncio
import atexit
import traceback
from functools import partial
from inspect import iscoroutinefunction
from threading import Thread
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
    "run_coroutine"
]

# ---- [ internal ] -----------------------------------------------------------

__loop: asyncio.AbstractEventLoop | None = None
__thread: Thread | None = None

if __loop is None:
    __loop = asyncio.new_event_loop()
    __thread = Thread(target=__loop.run_forever)
    __thread.daemon = True
    __thread.start()


@atexit.register
def on_exit():
    global __loop
    if __loop is None or __thread is None:
        return

    loop = __loop
    __loop = None

    def shutdown():
        for task in asyncio.all_tasks(loop):
            task.cancel()
        loop.stop()

    loop.call_soon_threadsafe(shutdown)
    __thread.join()
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()


# ---- [ public ] -------------------------------------------------------------


# Using pyright's type inference to get this type right – for now.
def debounced(delay_in_ms: int):
    """Schedule coroutine for execution as soon as no events arrive for given delay.

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
        call_at: dict[int, float] = {}

        def _debounced_callback(view: sublime.View, coro_func: Callable[[], BlankCoro]) -> None:
            """
            Callback running on event loop to debounced schedule coroutine execution

            :param view:
                The view handling the event for
            :param coro:
                The coroutine object to schedule
            """
            if __loop is None:
                del call_at[view.view_id]
                return

            if call_at[view.view_id] <= __loop.time():
                del call_at[view.view_id]
                if view.is_valid():
                    # Tasks are collected later using `asyncio.all_tasks`.
                    _ = __loop.create_task(coro_func())
                return

            __loop.call_at(call_at[view.view_id], _debounced_callback, view, coro_func)

        def wrapper(self: EventListener | ViewEventListener, *args: sublime.View) -> None:
            """
            Wrapper function called on UI thread to schedule debounced coroutine execution

            :param args:
                The arguments passed to coroutine function by ST API
            :param kwargs:
                The keywords arguments passed to coroutine function by ST API
            """
            if __loop is None:
                return

            view = self.view if isinstance(self, ViewEventListener) else args[0]
            vid = view.view_id
            pending = vid in call_at
            call_at[vid] = __loop.time() + delay_in_ms / 1000
            if pending:
                return

            __loop.call_soon_threadsafe(_debounced_callback, view, partial(coro_func, self, *args))

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
    if __loop is None:
        raise RuntimeError("No event loop running!")

    return asyncio.run_coroutine_threadsafe(coro, loop=__loop)


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


class AsyncEventListenerMeta(type):
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
        mcs: type[AsyncEventListenerMeta],
        name: str,
        bases: tuple[type, ...],
        attrs: dict[str, object],
    ) -> AsyncEventListenerMeta:
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


class EventListener(sublime_plugin.EventListener, metaclass=AsyncEventListenerMeta):
    """
    This class describes an asyncio event listener.

    It extends `sublime.EventListener` to support event handler coroutines
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


class ViewEventListener(sublime_plugin.ViewEventListener, metaclass=AsyncEventListenerMeta):
    """
    This class describes an asyncio view event listener.

    It extends `sublime.ViewEventListener` to support event handler coroutines
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
