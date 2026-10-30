# Media credits

- `drc-robot.jpg`: Team ViGIR robot "Florian" at the DARPA Robotics Challenge 2015. Defense Advanced Research Projects Agency, public domain. https://commons.wikimedia.org/wiki/File:Team_ViGIR_Robot_Florian_DARPA_Robotics_Challenge_2015_2.jpg
- `crash-bar-doors.jpg`: "Set of Crash Bar Doors" by Scott Brody, CC BY-SA 4.0. https://commons.wikimedia.org/wiki/File:Set_of_Crash_Bar_Doors.jpg
- `panic-bar.jpg`: "Panic bar" by Jonathan Clemens, CC BY-SA 3.0. https://commons.wikimedia.org/wiki/File:Panic_bar.jpg
- `guadalupe-comfort-2026-10-29.jpg`: the landing's river sample (screen 1, labelled "Sample" like the other examples; never refreshed). USGS camera "Guadalupe Rv at Comfort" (TX_Guadalupe_Rv_at_Comfort, gauge 08167000), captured 2026-10-29 19:41:21 UTC (2:41 PM CDT). U.S. Geological Survey, public domain. Source: https://apps.usgs.gov/hivis/camera/TX_Guadalupe_Rv_at_Comfort , file https://usgs-nims-images.s3.amazonaws.com/720/TX_Guadalupe_Rv_at_Comfort/TX_Guadalupe_Rv_at_Comfort___2026-10-29T19-41-21Z.jpg . Unmodified (720x407).
- `guadalupe-comfort-2026-10-29.json`: the gauge reading the river sample's answer quotes (80.6 ft³/s at 19:40 UTC, 2026-10-29) and USGS's daily percentiles for Oct 29 (median 100 ft³/s). USGS Water Data APIs, public domain; source URLs inside the file.

## ego-door.mp4
"A person opening the door" by Martina Tomšič. Source: https://www.pexels.com/video/a-person-opening-the-door-4010079/ . Licence: Pexels License, https://www.pexels.com/license/ . Trimmed (0.5–3.5 s, before the dog enters), scaled to 568x320, audio removed by re-encode.

## Training-loop edge cases

- `edge-snow.jpg`: "Skier walking across the slushy street carrying skis, snowy overcast day, intersection, car, windshield wipers, red light, Wallingford, Seattle, Washington, USA" by Wonderlane. Source: https://www.flickr.com/photos/71401718@N00/6762518093 . Licence: CC0 1.0 Public Domain Dedication, https://creativecommons.org/publicdomain/zero/1.0/ . Resized to 800 px wide.
- `edge-cyclist.jpg`: "Cyclist in traffic at dusk. Cairo." by Lars Plougmann. Source: https://www.flickr.com/photos/75062596@N00/52632412013 . Licence: CC BY-SA 2.0, https://creativecommons.org/licenses/by-sa/2.0/ . Resized to 800 px wide.
- `edge-flood.jpg`: "FEMA - 32096 - Cars drives though flooded street in an Oklahoma neighborhood" by Marvin Nauman / FEMA. Source: https://commons.wikimedia.org/wiki/File:FEMA_-_32096_-_Cars_drives_though_flooded_street_in_an_Oklahoma_neighborhood.jpg . Licence: Public domain (work of the US federal government), https://creativecommons.org/publicdomain/mark/1.0/ . Resized to 800 px wide (with a 0.3 px blur to stay under 120 KB).
- `edge-roadwork.jpg`: "Construction on the road at night" by Matthew Henry (via Unsplash). Source: https://commons.wikimedia.org/wiki/File:Construction_on_the_road_at_night.jpg . Licence: CC0 1.0, https://creativecommons.org/publicdomain/zero/1.0/ . Resized to 800 px wide.
- `edge-snow-2.jpg`: "Predlet 1.0 got her round and one other today so we drove around in this delivering papers." by Ian Hughes (epredator). Source: https://www.flickr.com/photos/53726379@N00/40574231411 . Licence: CC BY 2.0, https://creativecommons.org/licenses/by/2.0/ . Resized to 800 px wide.
- `edge-snow-3.jpg`: "2011_02_28" by Dennis Sylvester Hurd. Source: https://www.flickr.com/photos/43296902@N00/5487043474 . Licence: CC0 1.0 Public Domain Dedication, https://creativecommons.org/publicdomain/zero/1.0/ . Resized to 800 px wide.
- `edge-snow-4.jpg`: "PC010113" by codersquid. Source: https://www.flickr.com/photos/45421351@N00/311250810 . Licence: CC BY-SA 2.0, https://creativecommons.org/licenses/by-sa/2.0/ . Resized to 800 px wide.

## Hero examples (stand-ins, labelled "example" on the page)
- `ex-ramp.jpg`: "Sutera Mall - Wheelchair Ramp" by Chongkian. Source: https://commons.wikimedia.org/wiki/File:Sutera_Mall_-_Wheelchair_Ramp.jpg . Licence: CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0/ . Resized to 800 px wide.
- `ex-menu.jpg`: "Specials Board" by Taber Andrew Bain. Source: https://commons.wikimedia.org/wiki/File:Specials_Board_(521649450).jpg . Licence: CC BY 2.0, https://creativecommons.org/licenses/by/2.0/ . Resized to 800 px wide.
- `ex-roof.jpg`: "View of Highcombe from drone above garden" by Fennec Media Ltd. Source: https://commons.wikimedia.org/wiki/File:View_of_Highcombe_from_drone_above_garden.jpg . Licence: CC BY 4.0, https://creativecommons.org/licenses/by/4.0/ . Resized to 800 px wide.
- `ex-fields.jpg`: "Agricultural Fields Near Rome" by Sentinel Hub (contains modified Copernicus Sentinel data). Source: https://commons.wikimedia.org/wiki/File:Agricultural_Fields_Near_Rome_(50585926873).jpg . Licence: CC BY 2.0, https://creativecommons.org/licenses/by/2.0/ . Resized to 800 px wide. The "AIRBUS | sentinelhub" watermark in the bottom-left corner is the licensor's and stays: the landing pins the image to that corner (`img.wm`, object-position left bottom) so no crop removes it, and the img alt and title carry the credit "© Airbus DS / Sentinel Hub, contains modified Copernicus Sentinel data (CC BY 2.0)".

## Place names (src/places.json)
- `places.json` (read by `places.js`, not media, credited here with the site's other third-party material): cities of 50,000+ people and every national capital, with their countries and first-level divisions, from GeoNames (https://www.geonames.org/): cities15000, countryInfo and admin1CodesASCII. Licence: CC BY 4.0, https://creativecommons.org/licenses/by/4.0/ . Trimmed and reshaped by `tools/make_places.py` (coordinates rounded to 0.01°, population to thousands; a few common English names added, e.g. Cologne for Köln). The typed text is matched against this file inside the browser and never sent anywhere.
