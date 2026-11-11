# Local Model Desk

[简体中文](../README.md) · [Windows downloads](https://github.com/uyujkk/LocalModelDesk/releases/latest) · [License](../LICENSE)

Local Model Desk is a Windows launcher for dual-GPU Qwen3.8-Flash-Next on Strata. It starts and unloads models, keeps the selected OpenCode model and context in sync, embeds Strata telemetry, and provides per-card NVIDIA power controls.

## Install and start

1. Download the Windows x64 portable ZIP from Releases and check it against the accompanying SHA-256 file. GitHub's **Source code** links contain repository snapshots.
2. Extract the entire archive and run `LocalModelDesk.exe`. Keep its `_internal` directory alongside the executable. The ZIP includes the launcher's runtime, source, and documentation.
3. In **Settings**, select the existing Strata deployment root. The launcher targets `terminal/0.1.57/bin/strata.cmd`, the model data folder `models/`, and the OpenCode files `agent/Start-Qwen-Desktop.cmd`, `agent/desktop/OpenCode.exe` and `agent/config/opencode/opencode.json`. Strata, OpenCode, drivers and model weights are installed separately. The existing OpenCode launcher must read the listed configuration file.
4. Choose IQ2_XS or IQ3_XXS and a 32K–256K context. **Check download** previews downloads and resource guidance. **Start model and open OpenCode** starts the local service and backs up the OpenCode configuration before updating it. GPU 0 is the main card and GPU 1 is the helper.
5. To upgrade, close the old launcher and extract the new ZIP into a separate folder. Local Model Desk keeps its preferences in `%LOCALAPPDATA%\QwenDesk\Python\settings.json`. Save the interface language in **Settings** and reopen the app to apply it.

## Images, monitoring, and power

Enable image input on **Home**, check the download plan, and start or switch the model. First-time setup may download a vision encoder of about 0.9 GB. Open a new OpenCode chat and attach an image. The image tokens share the chosen context window with text. If attachment delivery fails, try the same picture in Strata's browser chat at `http://127.0.0.1:8080`.

The **Monitor** page shows prompt progress, output rate, GPU usage, and recent requests. **GPU Power** queries each card via the installed NVIDIA driver. Enter watts within its reported range, click **Apply**, and approve the Windows prompt when shown. The page reads the limit back after applying it. **Restore default** uses the driver's default for that card. Check the current limit again after a reboot or driver reload.

## Troubleshoot

- For missing Strata or OpenCode files, check the configured deployment root and paths above.
- The launcher uses port 8080. If a different service occupies it, stop that service from its original launcher.
- Check **Logs** for download and startup errors. On the same PC, query `http://127.0.0.1:8080/health` to check `model`, `max_context`, `loaded` and `images`.
- A vision setup can reuse the existing text weights. If the image state is off after launch, check the Strata log for the vision encoder.
- For power changes, check the driver's reported range and the administrator prompt. Driver and hardware support vary.

See the [context budget table and build commands](../README.md) for source development. The app source uses MIT. See [third-party notices](../THIRD-PARTY.md) for components in the portable package.
