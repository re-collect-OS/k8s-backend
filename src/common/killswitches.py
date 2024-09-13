# -*- coding: utf-8 -*-
from common import features

_features = features.get()

# List of global killswitches (i.e. affecting worker & server apps).

# When enabled, puts the system into maintenance mode:
# - public facing HTTP servers return 503 to all operations (except healthcheck)
# - workers stop processing all tasks
maintenance = _features.killswitch("maintenance-mode")

# Recurring imports read-only mode.
readwise_v2_readonly = _features.killswitch("readonly-readwise-v2-imports")
readwise_v3_readonly = _features.killswitch("readonly-readwise-v3-imports")
rss_readonly = _features.killswitch("readonly-rss-imports")
twitter_readonly = _features.killswitch("readonly-twitter-imports")
google_drive_readonly = _features.killswitch("readonly-google-drive-imports")
