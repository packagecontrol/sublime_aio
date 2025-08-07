# Contributing

## Install development version

Python packages are organized in _Lib/pythonXX_ path in Sublime Text's profile folder.

> [!NOTE]
> 
> **Linux**
> 
> ```sh
> "$HOME/.config/sublime-text/Lib/pythonXX"
> ```
> 
> **macOS**
> 
> ```sh
> "$HOME/Library/Application Support/Sublime Text/Lib/pythonXX"
> ```
> 
> **Windows**
> 
> ```bat
> "%APPDATA%\Sublime Text\Lib\pythonXX"
> ```
> 
> XX denotes the target python version.

1. git clone this repository to a folder out-side of Sublime Text.
2. create a symlink of _src/sublime_aio_ folder under _Lib/pythonXX_.
3. create an empty _sublime_aio-1.0.0.dist-info_ folder,
   if no one starting with _sublime_aio_ exists.

## Creating a Release

### Preparation

1. Merge all relevant PRs.
2. Ensure all tests pass.
3. Make sure `__version__` global in `src/sublime_aio.py` has up-to-date value.
   _It will be used to create the release version and wheel file!_

### via GitHub

1. Navigate to Actions https://github.com/sublimelsp/sublime_aio/actions
2. Select `Publish Release`
3. Click `Run workflow` button to start release workflow.

### via CLI

To create a relase via CLI, run:

   ```sh
   scripts/release
   ```

> [!NOTE]
> The workflow uses [uv][] and [gh][] utilities.
> Ensure [gh][] has push access to sublime_aio repository
> to be able to create releases.

[gh]: https://cli.github.com/
[uv]: https://docs.astral.sh/uv/
