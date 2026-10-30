# Privacy

Status: describes the code as built. Anything marked **(unverified)** has not been checked.

## TL;DR

- **The website** is static files. It has no API and sets no cookies, analytics or third-party scripts. Some pages contact a public service straight from your browser: the home page's river reading (USGS), the home page's "Get it now" (the free provider you picked), and the card checker (the address you give it). Never irlpls.
- **The reference library** runs on your machine. It sends place names to OpenStreetMap Nominatim to find coordinates, unless you give coordinates yourself or turn geocoding off.
- **There is no hosted irlpls service.** Your agent calls each provider directly.
- **Orders** go from your agent straight to the provider, using your own account with that provider. irlpls is not in the middle and never sees them.
- **Quotes from a protocol provider** (a card with a `protocol` block; none is listed yet) carry coarse coordinates (about 1 km), no address, no "why" and no identity; your exact place goes only to the provider you choose, when you order. **Quotes and reads through an adapter** (all 61 today) send the place at full precision to that provider.
- **The provider you order from** learns who you are (your account), your IP address, the exact place and what you asked for.

## The model

One provider sees only its own requests. A central router would see every request from everyone, so irlpls runs on the requester's side: the matcher (`match.js`, `irlpls/match.py`) and the broker run on your machine or inside your agent, and you talk to each provider directly. The project publishes cards and a standard, not a service in the middle.

## The website

Pages are plain files from irlpls.com. They set no cookies and load no analytics, third-party scripts or third-party fonts. Matching on the site (`match.js`) runs in your browser.

These pages contact someone else from your browser. Each request goes from your browser straight to that service, never through irlpls, and each service sees your IP address and user agent, as with any site:

- **Home page, first screen:** fetches the latest flow for USGS site 08167000 (Guadalupe River at Comfort, TX) from `api.waterdata.usgs.gov`. The river photos are saved copies served from irlpls.com.
- **Home page, "Get it now"** (`live.js`): when you tap it on a free match, your browser asks that one provider for a reading. It sends the map point of the place your ask was matched to (rounded to about 100 m, or a box around it), or a station id; the text you typed is not sent. Matching the ask to a place runs in your browser, from a built-in list of cities. The providers:

| Provider | Host | Sent |
|---|---|---|
| USGS stream gauges | `api.waterdata.usgs.gov` | A box about 40 km around the point, then the chosen gauge's id (for its name) |
| US National Weather Service | `api.weather.gov` | The point, then the nearest station's id |
| UK Environment Agency flood monitoring | `environment.data.gov.uk` | The point and a 15 km radius, then a station id |
| PEGELONLINE (German federal waterways) | `pegelonline.wsv.de` | The point and a 25 km radius |
| Digitraffic / Fintraffic (Finnish ships and road cameras) | `meri.digitraffic.fi`, `tie.digitraffic.fi`, `weathercam.digitraffic.fi` | Ships: the point and a 20 km radius. Road cameras: the station list, then one station's id and its image |
| NOAA Tides & Currents | `api.tidesandcurrents.noaa.gov` | The water-level station list, then the nearest station's id (and `application=irlpls`) |
| USGS Earthquakes | `earthquake.usgs.gov` | The point, a 250 km radius and a start time 24 h ago |
| EMSC | `www.seismicportal.eu` | The point, a 250 km radius and a start time 24 h ago |
| GeoNet (New Zealand) | `api.geonet.org.nz` | Nothing about you: the latest quake list, filtered in your browser |
| Hub'Eau hydrometry (France) | `hubeau.eaufrance.fr` | The point and a 15 km radius, then the nearby station ids |
| PEGELONLINE, Austria | `pegelonline.wsv.de` | One Danube gauge's id (Korneuburg for Vienna, Wilhering for Linz, Dürnstein for Krems) |
| Sensor.Community | `data.sensor.community` | The point and a 5 km radius |
| AirNow | `files.airnowtech.org` | Nothing about you: the national reporting-area file, searched in your browser |
| TfL JamCams | `api.tfl.gov.uk`, `s3-eu-west-1.amazonaws.com` | The point and a 2 km radius, then the nearest camera's image |
| Finnish Meteorological Institute | `opendata.fmi.fi` | A box about 30 km around the point |

- **Card checker:** fetches the domain you type, or the card you paste, and, under "Check a sample pack", the sample-pack links that card lists (an `evidence_url` ending in `irlpls-sample.json`) and the files each pack names. Other evidence links are listed, not fetched.

The static host's own access logs (IP, user agent, URL, time) depend on the host, which is not chosen yet **(unverified)**.

## The reference library

Everything runs in your process. Outbound calls use `irlpls/http.py` (user agent `irlpls/0.1 (+https://irlpls.com)`), and each host contacted sees your IP address. Nothing goes to irlpls.com except fetching its static files.

**Geocoding.** When a request names a place and has no coordinates, `irlpls/geo.py` sends the place text to Nominatim over HTTPS (`nominatim.openstreetmap.org/search`). If you write a home address, Nominatim (a third party) receives it. Results are cached in memory for the session. Nominatim's logging follows the OSMF policy **(unverified here)**. To avoid it:

- give coordinates: `irlpls.request(need, lat=..., lon=...)` or `irlpls.find(..., lat=..., lon=...)`, or
- turn it off: `geocode=False` (`irlpls find --no-geo` on the command line).

**Saved jobs.** The broker writes each job (need, why, plan, quotes, order ids) to `~/.irlpls/jobs/` on your machine, or to `$IRLPLS_HOME`. It is not sent anywhere.

**Providers with an adapter** (no protocol). Each adapter talks only to the provider named in its file, and sends the place at full precision:

| Category | Adapters | Sent, and to whom |
|---|---|---|
| Open sensor / public data (no key) | `nws`, `noaa_tides`, `usgs_water`, `usgs_earthquakes`, `emsc_seismicportal`, `geonet_nz`, `nasa_firms`, `ndbc_buoys`, `hubeau_hydrometrie`, `digitraffic_marine_ais`, `fmi_open_data`, `uk_ea_flood`, `pegelonline`, `sensor_community`, `fintraffic`, `caltrans_cctv`, `alertcalifornia`, `airnow`, `tfl_jamcams`, `wsdot_cameras` | Coordinates, a bounding box, or a station or region ID, to that agency's host. `nasa_firms` and `tfl_jamcams` add your key only if you set one. |
| Keyed sensor / webcam APIs | `openaq`, `purpleair`, `windy_webcams`, `empiric_earth` | Coordinates plus your API key, to the provider. |
| Satellite / aerial / drone catalogues and orders | `copernicus`, `planet`, `skyfi`, `umbra`, `spexi`, `beemaps`, `up42`, `skywatch`, `pixxel`, `satvu`, `iceye`, `satellogic`, `capella`, `vantor_hub`, `open_cosmos_datacosmos`, `globhe`, `flyguys`, `zeitview` | Search area or order geometry, dates and your credentials, to the provider. |
| People-based capture (marketplaces, field and inspection services, data collection) | `proxypics`, `prolific`, `rentahuman`, `human_for_ai`, `errand`, `getterdone`, `mundane`, `toloka`, `lxt_clickworker`, `hire_a_human`, `airtasker`, `quest`, `hover`, `snappr`, `field_nation`, `truepic_vision`, `defined_ai` | Task text, location and your account or key, to the provider. Its workers or contractors see the location; `truepic_vision` and `snappr` also send the contact details you give for the person on site. |
| Wallet-paid bounties (no account) | `vangrid`, `execution_market` | Task text, location and the amount, to the provider. Paid calls are signed by your own wallet (prepared here, never signed by irlpls), so the wallet address and payment are public on chain. |

Orders are placed only after a policy check (`auto_submit`, `max_order_usd`); a provider's second, paid step (Capella's cost estimate, GLOBHE's quotes) only after your explicit approval of that order, never under `auto_submit`. Searching and quoting place no order but still send your search area. This table is a summary, not a field-by-field audit **(unverified)**; the exact payloads are in `irlpls/adapters/<name>.py`.

## Providers that speak the protocol

For a provider whose card has a `protocol` block (STANDARD Section 14), the library calls the provider's own endpoint, using the credentials you configured for it (`broker.PROTOCOL["auth"]`). Those credentials never leave your process except to that provider.

**Quotes** (`irlpls/broker.py`, `_wire_request`) send:

- the `need` text as you wrote it, plus timing, quantity, mode, terms and the country code;
- coordinates rounded to 0.01° (about 1 km), and no `place` field;
- a `targets_person` flag when the request is about a person.

They do not send your Policy, your `why`, or who you are. Before your first order with that provider, it also does not know your account, unless its quote endpoint requires your credentials **(per provider, unverified)**.

**The exact place** (full coordinates and the `place` text) goes only to the provider you chose: in its own re-quote and in the order. Live readings use the coarse form.

**Orders** go from your agent directly to `<the card's protocol endpoint>/orders`, under your own provider account. They carry the quote or request, a spending ceiling, a random nonce, a random job id as `client_reference`, and whether a person approved or a standing mandate covered it.

**Free text is not cleaned.** If `need` names a street or a person, the provider gets that text even in a coarse quote. To keep quotes coarse, keep the address out of `need` and give it as `place` or coordinates.

## The MCP server

`irlpls-mcp` runs the same library inside your agent's MCP host, so everything above applies.

- **Full mode** (default): can plan, quote and order. It geocodes place names with Nominatim, and orders with this process's provider credentials. Run it locally (stdio, or HTTP on 127.0.0.1). Binding another address needs `--allow-remote`, and then anyone who can reach the port can use the ordering tools, since the HTTP transport has no authentication.
- **Read-only mode** (`--read-only`): plans only. `irlpls_submit` and the six protocol tools are not registered, so it makes no provider calls and cannot quote or order.

## Running a read-only server yourself

There is no hosted irlpls service. If you run the read-only MCP server for others, `deploy/serve.py` is the only supported way. It:

- refuses to start without `--read-only`;
- turns geocoding off, so it calls no one (place words and coverage text still match; agents can pass `lat`/`lon`);
- turns the access log off and sets server logs to WARNING, so no IP, path, request body or tool argument is logged;
- caps request size (64 KB) and live sessions (500).

Plans are held in memory and are lost on restart. A job id is the only key to a plan, so anyone holding the id can read it. The container user has no writable home, so the library's job files (`~/.irlpls/jobs/`) are not written **(not tested in the built image)**. The hosting platform's own request logs are set by that platform.

## What each party learns

| Who | Learns |
|---|---|
| irlpls (project) | Nothing about your requests. Static-file fetches only. |
| Nominatim | Each place name you geocode, and your IP. None when you give coordinates or turn geocoding off. |
| A free provider you tap "Get it now" on (website) | The map point or station it is asked about, and your IP. Not the text you typed. |
| A protocol provider you only quote | `need` text, coarse coordinates, country, timing, your IP. |
| The protocol provider you order from | Also: the exact place, your account, the order and its approval, your IP, any webhook host. What it keeps is up to its own policy. |
| An adapter provider | The place at full precision, your key or account, your IP. |
| A capture marketplace's workers | The task and the location. |

## Keep it local

1. Give coordinates or pass `geocode=False`, so no place string goes to a geocoder.
2. Keep addresses and names out of `need`.
3. Load only the adapters and provider credentials you need.
4. Self-host the static files if you don't want to fetch `cards.json` and `match.js` from irlpls.com.

## Questions

Security reports: `SECURITY.md`. What the project will not do: `GOVERNANCE.md`. The normative privacy rules are in `docs/STANDARD.md` Sections 12 and 14.12. Contact: hello@irlpls.com.
