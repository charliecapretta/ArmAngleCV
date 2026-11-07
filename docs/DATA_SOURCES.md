# Data sources and attribution

## Baseball Savant / MLB Statcast

The arm-angle targets, pitch identifiers, pitcher information, and game metadata
come from [Baseball Savant](https://baseballsavant.mlb.com/), MLB's Statcast service.
The [Statcast CSV field reference](https://baseballsavant.mlb.com/csv-docs)
documents the source fields. `data/savant_data.csv` is the local export used for
development, spanning August 10–16, 2026. The source export, labels, and manifest
are research inputs; this project is not affiliated with or endorsed by MLB.

`labels.csv` and `data/manual-v1/labels.csv` combine those source identifiers and
targets with manually selected release frames and manually clicked image points.
See [MODEL_CARD.md](MODEL_CARD.md) for snapshot counts and intended use.

## Video

No MLB broadcast video is included in the public file set. `data/uiTestBatch.json`
records the fixed example identifiers and source metadata. The optional downloader
retrieves clips from their Baseball Savant pages for local use. Availability can
change. Keep attribution with any externally sourced material and consult its
source terms before redistribution.

The hosted app can offer one practice clip to visitors, installed on the server
with `hosted.manage example install` and never part of the public file set.
Installing one serves that footage to everyone who opens the site, which goes
beyond the local research use the downloader is meant for. Confirm redistribution
is permitted before installing a clip, and `example remove` withdraws it.

Personal videos, their registrations, and their prediction reports are excluded
from the public release. The application reads user-selected files locally.

## Code, models, and dependencies

Project code is offered under the [MIT license](../LICENSE), selected by the owner.
That code license does not purport to relicense MLB data, video, or third-party
dependencies and pretrained models. Dependencies retain their own licenses.
The two small Ridge model files are project-generated scikit-learn artifacts;
the normalized model's training provenance is recorded in the model card.
Experimental third-party neural-network weights are not included.
