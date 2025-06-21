# AsyncIO for Sublime Text

## Common Notes

```py
import sublime_aio
```

First plugin importing `sublime_aio` starts a global event loop
in a dedicated background thread, which runs forever
and is shared across plugins.

Commands and event handlers are dispatched asynchronously,
causing no messurable delay on UI thread.

> [!NOTE]
> 
>  ST's asynchronous event handler methods (e.g. `on_modified_async`)
>  are not supported as task scheduling in UI thread is faster.


## Event Listener

This package demonstrates power of asyncio and [simdjson](https://pypi.org/project/pysimdjson/)
to handle hundrets of thousands of completions smoothly,
without blocking Sublime Text's UI in any way.

Both, `EventListener` and `ViewEventListener` are supported.

```py
import simdjson
import sublime_aio


class CompletionListener(sublime_aio.ViewEventListener):
    parser = simdjson.Parser()

    async def on_query_completions(self, prefix, locations):
        doc = self.parser.parse(data)
        return (i["label"] for i in doc["items"])

    async def on_modified(self):
        print(f"{self.view!r} got modified on io loop!")

    @sublime_aio.debounced(200)
    async def on_selection_modified(self):
        print(f"Selection of {self.view!r} got modified on io loop!")
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


## Run coroutines from synchonous code

Use `sublime_aio.run_coroutine()` to call a coroutine from synchronous code.

It will be executed on event loop's background thread.

```py
import sublime_aio

async def init1():
    await asyncio.sleep(1.5)
    print("Init Task 1 done!")

async def init2():
    await asyncio.sleep(1.0)
    print("Init Task 2 done!")

async def init_plugin():
    print("Initializing plugin...")
    task1 = asyncio.create_task(init1())
    task2 = asyncio.create_task(init2())
    await asyncio.gather(task1, task2)

def plugin_loaded():
    def on_done(fut):
        print("All up and running!")

    # Initialize plugin on asyncio event loop
    sublime_aio.run_coroutine(init_plugin()).add_done_callback(on_done)
```


## Window

The library provides a `Window` class based on `sublime.Window`
to provide some asyncio complient methods.

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

To await input of input panels within asyncio complient commands,
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

To await selected quick panel items within asyncio complient commands,
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
