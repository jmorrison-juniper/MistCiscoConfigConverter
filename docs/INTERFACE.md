# Interface walkthrough

These PNG files are actual Chromium captures of the Flask application on
localhost. The captures use the default T-Mobile theme and a 1440 by 1000
desktop viewport. They contain no live credentials or customer configuration.
The [sample file](examples/documentation-branch.cfg) uses example addresses.
The sample input is synthetic; the interface and its responses are not.

## Landing page

The application shows the file input controls and an API-not-configured status.
No Mist organization is connected.

![Landing page](screenshots/landing.png)

## File and gateway selection

Upload the sample file, then select Branch and SRX. The Convert to Mist button
becomes available.

![File and gateway selection](screenshots/selection.png)

## Configuration review

Select Display Only to inspect the parsed Cisco configuration.

![Configuration review](screenshots/config-review.png)

## Conversion proposal

Select Convert to Mist to view the proposal. This screen shows the local
parser's output. Without Mist credentials, cloud object checks cannot verify
whether a site, template, network, or service already exists.

The capture shows the end of the proposal dialog. Below the proposal, a text
field asks for the word `APPLY`. The Confirm & Apply button stays disabled until
you type the word. The server rejects an apply request that does not contain the
word. Refer to [Apply confirmation](USER_GUIDE.md#apply-confirmation).

![Conversion proposal](screenshots/proposal.png)

Do not type the confirmation word during an offline review. Applying settings
requires a configured Mist organization and changes cloud objects.

## Capture procedure

1. Install the runtime dependencies in a Python 3.13 virtual environment as
   described in the [user guide](USER_GUIDE.md#local-development-without-container).
2. Start Flask on localhost with no API token and no organization ID.
   The command below is for a POSIX shell. Use an isolated checkout without
   a `.env` file. Do not use a checkout that contains live credentials.
3. Open the page in Chromium at a 1440 by 1000 viewport. Wait for the connection
   status to finish loading, then capture the landing page.
4. Upload `docs/examples/documentation-branch.cfg`. Select Branch and SRX, then
   capture the selection screen.
5. Select Display Only, wait for the parsed result, then capture the page.
6. Select Convert to Mist and wait for the proposal. Scroll the dialog to its
   end, then capture it. Do not type the confirmation word, and do not apply.

```bash
MIST_APITOKEN='' org_id='' MIST_HOST=127.0.0.1 POWERUSER=false \
  THEME=tmobile .venv/bin/python -m flask --app app run \
  --host 127.0.0.1 --port 8765
```

The committed captures were made with Playwright Chromium. Requests to hosts
other than `127.0.0.1` were blocked in the browser. Each allowed request reached
the real Flask server; no HTML or API response was replaced. The server had
empty Mist credentials and its Mist host was set to localhost. No Mist API
calls or apply operations were made.
