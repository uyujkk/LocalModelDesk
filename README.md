# Local Model Desk

[简体中文](docs/README.zh-CN.md) · [Windows downloads](https://github.com/uyujkk/LocalModelDesk/releases/latest) · [License](LICENSE)

Local Model Desk is a Windows launcher for dual-GPU Qwen3.8-Flash-Next on Strata. It starts and unloads models, keeps the selected OpenCode model and context in sync, embeds Strata telemetry, and provides per-card NVIDIA power controls.

![Local Model Desk workbench with IQ3_XXS, 128K context, and CPU image input](docs/workbench.png)

*Workbench shown with a simulated local service.*

## Install and start

1. Use the Windows x64 portable ZIP for this build and check it against the accompanying SHA-256 file. GitHub's **Source code** links contain repository snapshots.
2. Extract the entire archive and run `LocalModelDesk.exe`. Keep its `_internal` directory alongside the executable. The ZIP includes the launcher's runtime, source, and documentation.
3. In **Settings**, select the existing Strata deployment root. The launcher targets `terminal/0.1.57/bin/strata.cmd` and the model data folder `models/`. It finds OpenCode Desktop in the deployment or in the Windows installation. Enter the exact executable in Settings to override discovery. Strata, OpenCode, drivers and model weights are installed separately.
4. Choose IQ2_XS or IQ3_XXS and a 32K–256K context. **Check download** previews downloads and resource guidance. **Start model and open OpenCode** starts the local service and backs up the OpenCode configuration before updating it. GPU 0 is the main card and GPU 1 is the helper.
5. To upgrade, close the old launcher and extract the new ZIP into a separate folder. Local Model Desk keeps its preferences in `%LOCALAPPDATA%\QwenDesk\Python\settings.json`. Save the interface language in **Settings** and reopen the app to apply it.

## OpenCode Desktop 2

OpenCode Desktop 2.2.6 or later uses a separate configuration and session directory under `agent/opencode-v2/`. Local Model Desk opens the installed desktop directly, configures the selected Strata model, and loads its bundled `strata-charts` GUI plugin. If an OpenCode background service is already using another configuration, finish its active tasks and accept the restart prompt. On OpenCode 1.x the existing deployment launcher and configuration are still used.

Ask for an interactive chart in chat. The plugin renders a completed `strata-chart` block as a bar chart with a switch for each supplied metric. For example, ask it to chart two sample decode rates and VRAM values in `strata-chart` format. The plugin can be inspected under **Settings → Extensions → Plugins** in OpenCode Desktop.

## Context budgets

| Strata context | OpenCode input budget | Output cap | Default compaction reserve | Default compaction threshold |
|---|---:|---:|---:|---:|
| 32K | 28K | 4K | 2K | 26K |
| 64K | 56K | 8K | 4K | 52K |
| 128K | 112K | 16K | 8K | 104K |
| 256K | 224K | 32K | 16K | 208K |

K means 1,024 tokens. The threshold assumes automatic compaction is enabled and uses the default reserve. The output cap includes reasoning tokens. Available system memory and VRAM affect how much of the configured context you can use comfortably.

## Images, monitoring, and power

Enable image input on **Home**, check the download plan, and start or switch the model. First-time setup may download a vision encoder of about 0.9 GB. Open a new OpenCode chat and attach an image. The image tokens share the chosen context window with text. If attachment delivery fails, try the same picture in Strata's browser chat at `http://127.0.0.1:8080`.

The **Monitor** page shows prompt progress, output rate, GPU usage, and recent requests. **GPU Power** queries each card via the installed NVIDIA driver. Enter watts within its reported range, click **Apply**, and approve the Windows prompt when shown. The page reads the limit back after applying it. **Restore default** uses the driver's default for that card. Check the current limit again after a reboot or driver reload.

## Troubleshoot

- For missing Strata or OpenCode files, check the configured deployment root and paths above.
- The launcher uses port 8080. If a different service occupies it, stop that service from its original launcher.
- Check **Logs** for download and startup errors. On the same PC, query `http://127.0.0.1:8080/health` to check `model`, `max_context`, `loaded` and `images`.
- A vision setup can reuse the existing text weights. If the image state is off after launch, check the Strata log for the vision encoder.
- For power changes, check the driver's reported range and the administrator prompt. Driver and hardware support vary.

## Screenshots

### Embedded Strata monitor

Prompt progress, recent requests, and dual-GPU telemetry. The readings shown are simulated.

![Embedded Strata dashboard with simulated dual-GPU telemetry and prompt progress](docs/strata-monitor.png)

### GPU power limits

Per-card power draw, allowed limits, and restore controls. The readings shown are simulated.

![GPU Power page with simulated readings for two NVIDIA cards](docs/gpu-power.png)

### Settings

Strata installation path, appearance, and interface language.

![Settings page in dark mode](docs/settings.png)

## Build and test from source

Use Python 3.12 on Windows x64. Run these commands in PowerShell from the repository root.

```powershell
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r .\source\requirements.txt
& .\.venv\Scripts\python.exe .\source\app.py
& .\.venv\Scripts\python.exe -m unittest discover -s .\source
& .\.venv\Scripts\python.exe .\source\app.py --smoke-test .\validation
& .\source\Build.ps1 -Python .\.venv\Scripts\python.exe
```

The portable build is written to `dist/LocalModelDesk/`. The smoke test uses simulated GPU writes and service state.

## License

The application source is available under the [MIT license](LICENSE). The embedded Overview renderer retains the [original Strata notice](source/strata_overview/LICENSE.txt). See the [third-party notices](THIRD-PARTY.md) for the portable distribution.
