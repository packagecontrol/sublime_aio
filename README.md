# AsyncIO for Sublime Text

## Common Notes

```py
import sublime_aio
```

First plugin importing `sublime_aio` starts a global event loop
in a dedicated background thread, which runs forever
and is shared across plugins.

Commands and event handlers are dispatched asynchronously,
causing no measurable delay on UI thread.

> [!NOTE]
>
>  ST's asynchronous event handler methods (e.g. `on_modified_async`)
>  are not supported as task scheduling in UI thread is faster.


## Event Listener

This package demonstrates power of asyncio and [simdjson](https://pypi.org/project/pysimdjson/)
to handle hundreds of thousands of completions smoothly,
without blocking Sublime Text's UI in any way.

Both, `EventListener` and `ViewEventListener` are supported.

```py
import simdjson
import sublime_aio


class CompletionListener(sublime_aio.ViewEventListener):
    parser = simdjson.Parser()

    async def on_query_completions(self, prefix, locations):
        doc = await sublime_aio.run_in_worker(self.parser.parse(data))
        return (i["label"] for i in doc["items"])

    async def on_modified(self):
        print(f"{self.view!r} got modified on io loop!")

    @sublime_aio.debounced(200)
    async def on_selection_modified(self):
        print(f"Selection of {self.view!r} got modified on io loop!")
```

> [!WARNING]
>
> To provide a transparent as native as possible experience,
> all event handler coroutines are wrapped into synchronous methods
> of same name for ST to be able to hook them.
>
> As a result those coroutines can't be awaited from other coroutines,
> once a Listener is instantiated.
>
> Do **not** call those synchronous handler methods instead,
> as it breaks the coroutine chain poking around between threads,
> which may cause all sorts of issues and overheads!!!
>
> All ST `on_...` event handler coroutines are pure end points,
> not designed to be re-used by plugin code!


### Completion Cancellation

Any pending `on_query_completions` coroutine is cancelled,
as soon as a new event is received from Sublime Text,
before a new completion query task is scheduled.

To send cancellation notificaitons to external applicaitons,
catch `asyncio.CancelledError`.

```py
import sublime_aio


class CompletionListener(sublime_aio.ViewEventListener):

    async def on_query_completions(self, prefix, locations):
        try:
            return await get_completions_from_server()
        except asyncio.CancelledError:
            await send_cancel_notification_to_server()
            return []
```


## Text Change Listener

```py
import sublime_aio

class TextChangeListener(sublime_aio.TextChangeListener):

    async def on_text_changed(self, changes):
        print(f"{self.buffer!r} changed: {changes}")

    async def on_reload(self):
        print(f"{self.buffer!r} reloaded")

    async def on_revert(self):
        print(f"{self.buffer!r} reverted")
```


## Application Commands

Replace `sublime_plugin.ApplicationCommand` by `sublime_aio.ApplicationCommand`
to implement commands running asynchronously on global event loop.

```py
import sublime_aio
import sublime_plugin


class NameInputHandler(sublime_plugin.TextInputHandler):

    def placeholder(self):
        return "Enter your name"


class MyAsyncCommand(sublime_aio.ApplicationCommand):

    def input_description(self):
        return "Name:"

    def input(self, args):
        if "name" not in args:
            return NameInputHandler()

    async def run(self, name):
        print(f"Hello {name}!")
```

Corresponding _Default.sublime-commands_

```json
[
    {
        "caption": "My Async Command",
        "command": "my_async"
    },
]
```


## Window Commands

```py
import sublime_aio
import sublime_plugin


class NameInputHandler(sublime_plugin.TextInputHandler):

    def placeholder(self):
        return "Enter your name"

class MyAsyncWindowCommand(sublime_aio.WindowCommand):

    def input_description(self):
        return "Name:"

    def input(self, args):
        if "name" not in args:
            return NameInputHandler()

    async def run(self, name):
        print(f"Hello {name} on {self.window}!")
```

Corresponding _Default.sublime-commands_

```json
[
    {
        "caption": "My Async Window Command",
        "command": "my_async_window"
    },
]
```


## View Commands

Due to asynchronous command execution and current ST API restrictions,
`sublime_aio` can't provide an asynchronous `TextCommand`.

The `edit` token required for text manipulation is only valid
during synchronous command execution.

Instead a `sublime_aio.ViewCommand` is provided
to implement view-specific commands
which do not directly alter content.

```py
import sublime_aio
import sublime_plugin


class NameInputHandler(sublime_plugin.TextInputHandler):

    def placeholder(self):
        return "Enter your name"

class MyAsyncViewCommand(sublime_aio.ViewCommand):

    def input_description(self):
        return "Name:"

    def input(self, args):
        if "name" not in args:
            return NameInputHandler()

    async def run(self, name):
        print(f"Inserting \"{name}\" to {self.view} on io loop!")
        self.view.run_command("insert", {"characters": name})
```

Corresponding _Default.sublime-commands_

```json
[
    {
        "caption": "My Async View Command",
        "command": "my_async_view"
    },
]
```


## Window

The library provides a `Window` class based on `sublime.Window`
to provide some asyncio compliant methods.

Note: Most of ST's API functions are fast enough to not need to be coroutines!

```py
class Window(sublime.Window):

    async def show_input_panel(
        self,
        caption: str,
        initial_text: str = "",
        on_change: Callable[[sublime.View, str], Coroutine[object, object, T]] | None = None,
    ) -> str:
        ...

    async def show_quick_panel(
        self,
        items: list[str] | list[list[str]] | list[sublime.QuickPanelItem],
        flags: sublime.QuickPanelFlags = sublime.QuickPanelFlags.NONE,
        selected_index: int = -1,
        on_highlight: Callable[[int], Coroutine[object, object, T]] | None = None,
        placeholder: str | None = None,
    ) -> int:
        ...
```


### Using Input Panels

To await input of input panels within asyncio compliant commands,
use `await sublime_aio.Window.show_input_panel()`.

The coroutine returns selected index or raises `InputCancelledError`,
if it was closed via <kbd>escape</kbd>`.

```py
class AsyncInputCommand(sublime_aio.WindowCommand):
    async def run(self):
        try:
            text = await self.window.show_input_panel(
                caption="Async Input",
                on_change=self.on_change
            )
            print("got", text)
        except sublime_aio.InputCancelledError:
            print("input cancelled")

    async def on_change(self, view: sublime.View, text: str) -> None:
        """Optional content changed event handler"""
        print(f"{view}: content changed to {text}")
```


### Using Quick Panels

To await selected quick panel items within asyncio compliant commands,
use `await sublime_aio.Window.show_quick_panel()`.

The coroutine returns selected index or raises `InputCancelledError`,
if it was closed via <kbd>escape</kbd>`.

```py
class AsyncQuickPanelCommand(sublime_aio.WindowCommand):
    async def run(self):
        try:
            index = await self.window.show_quick_panel(["foo", "bar"], on_highlight=self.on_highlight)
            print("got", index)
        except sublime_aio.InputCancelledError:
            print("quick panel cancelled")

    async def on_highlight(self, index: int) -> None:
        """Optional item selection changed event handler"""
        print("selected", index)
```


## Bridging between synchronous and asynchronous code

> [!NOTE]
>
> The functions in this section are primarily intended
> to cross-communicate between threads and synchronous
> and asynchronous code and thus do some extra work
> to ensure thread safety.
>
> Do not use them to invoke coroutines from within
> coroutines, especially not in same event loop!

### Run coroutines in event loop

Use `sublime_aio.run_coroutine()`
to schedule a coroutine for execution in event loop
from synchronous code in thread safe manner
and return a `concurrent.futures.Future` object,
holding execution results,
once coroutine is finished.

Example:

```py
import sublime_aio

async def async_func(arg1, arg2):
    return "Hello World"

def sync_func(arg1, arg2):

    def on_done(fut):
        exc = fut.exception()
        if exc is not None:
            traceback.print_exception(exc)
        else:
            print(fut.result())

    future = sublime_aio.run_coroutine(async_func(arg1, arg2))
    future.add_done_callback(on_done)
```

> [!NOTE]
>
> The caller is responsible for evaluating the result,
> including any exception raised during execution.


### Call coroutines in event loop

Use `sublime_aio.call_coroutine()`
to schedule a coroutine for execution in event loop
from synchronous code in thread safe manner
without creating future object.

_A lightweight fire and forget variant to invoke coroutines,
the result of which is not of any interest._

Example:

```py
import sublime_aio

async def async_func(arg1, arg2):
    print("Hello World")

def sync_func(arg1, arg2):
    sublime_aio.call_coroutine(async_func(arg1, arg2))
```

> [!NOTE]
>
> Traceback of uncaught exceptions is printed to console.


### Call functions in event loop

Use `sublime_aio.call_soon_threadsafe()`
to schedule a function for execution in event loop
from synchronous code in thread safe manner
without creating future object.

Example:

```py
import sublime_aio

def any_func(arg1, arg2):
    print(f"{arg1} {arg2}!")

def plugin_loaded()
    sublime_aio.call_soon_threadsafe(any_func, "Hello", "World")
```

> [!NOTE]
>
> Traceback of uncaught exceptions is printed to console.

> [!WARNING]
>
> This function exists to help migrating to asyncio compliant implementation.
>
> Exclusively use `async` and `await` in final implementation.
>
> Scheduling synchronous functions this way
> was how legacy python 3.3 used to think of async code.
>
> This is no longer best practice in modern python.


### Run CPU bound functions in worker threads

The main event loop should primarily be used
to execute cheap and short running functions
to ensure low latency of concurrent tasks.
Expensive CPU bound functions or blocking API calls
should be outsourced to one of the default worker threads.

Use `sublime_aio.run_in_worker()`
to a run a synchronous function in one of the default worker threads
and await the result via returned `asyncio.Future` object.

Example:

```py
import os
import sublime_aio

def blocking_func(arg1):
    return os.listdir(arg1)

async def async_func()
    result = await sublime_aio.run_in_worker(blocking_func, "~/Documents")
```

> [!TIP]
>
> It is equivalent to calling
> `asyncio.get_running_loop().run_in_executor(executor=None, func=blocking_func, "~/Documents")`
