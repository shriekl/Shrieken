# Shriek Luna

Trendline breakout signals for the Aster + KCEX coin universe. The scanner runs on GitHub Actions. The website is GitHub Pages. Signals are encrypted before they are published. The page asks for a password and decrypts them in the browser.

The password is the Actions secret `SITE_PASSWORD`. It is not stored in this repository.

## What you need to do once

1. On GitHub, open **Settings → Secrets and variables → Actions → New repository secret**.
2. Name the secret exactly `SITE_PASSWORD`.
3. Put the password you want to type on the website. Pick a long one. Anyone who can open the public site still needs this password.
4. Open **Settings → Actions → General → Workflow permissions** and choose **Read and write permissions**. This lets the scanner keep the last results between runs.
5. Open **Settings → Pages → Build and deployment** and set **Source** to **GitHub Actions**. The scan workflow also tries to turn this on by itself. If a run says Pages is not enabled, set it here and run the workflow again.
6. Open the **Actions** tab, choose **Scan signals**, and click **Run workflow**. Leave the box as `due`. The first run scans every timeframe and can take about 15–20 minutes.
7. When the run is green, open the site and enter the same password: <https://shriekl.github.io/Shrieken/>

Scheduled scans start by themselves after that. They only run from the default branch (`main`).

Changing the password later: update the `SITE_PASSWORD` secret, then run **Scan signals** with `all` in the timeframes box so every file is rewritten.

## Scan times

Candles are scanned about two minutes after they close. Times below are UTC.

| Timeframe | When it scans |
| --- | --- |
| 15m | every 15 minutes (`:02`, `:17`, `:32`, `:47`) |
| 30m | every 30 minutes (`:02`, `:32`) |
| 1h | once an hour (`:02`) |
| 4h | every 4 hours (`00:02`, `04:02`, `08:02`, `12:02`, `16:02`, `20:02`) |
| 12h | twice a day (`00:02`, `12:02`) |
| 1d | once a day (`00:02`) |
| 3d | every 3 days at `00:02` |
| 1w | Mondays at `00:02` |
| 1M | the 1st of the month at `00:02` |

If GitHub skips a run, a later run scans anything that is overdue. The site shows the last scan and the next scan in India time (IST).

## How the data is protected

Each scan is encrypted with AES-GCM. The key comes from the password through PBKDF2-SHA256 (100,000 rounds). The public files are ciphertext plus scan times. If `SITE_PASSWORD` is missing, the scan workflow stops and does not publish a file.

The page remembers the password in this browser until you press **Lock**.

Charts load live candles in the browser from Binance's public data API, and from MEXC if Binance is blocked. GitHub's scanners do the same: one failed Binance check switches that run to MEXC.
