# Deploy

This directory holds the Alas installer.

Install Alas by running `python -m deploy.installer` in Alas root folder. The
installer exports `requirements.txt` from `uv.lock`, then synchronizes the
current Python environment with `uv`. It looks for `toolkit/uv.exe` first and
falls back to `uv` from `PATH`; `UvExecutable` can override the location in
`config/deploy.yaml`.



# Launcher

Launcher `Alas.exe` is a `.bat` file converted to `.exe` file by [Bat To Exe Converter](https://f2ko.de/programme/bat-to-exe-converter/).

If you have warnings from your anti-virus software, replace `alas.exe` with `deploy/launcher/Alas.bat`. They should do the same thing.

