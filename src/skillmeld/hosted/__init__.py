# SPDX-License-Identifier: Apache-2.0
"""Build-time pipeline: crawl, build, sign, and publish the hosted data artifacts.

Not shipped to runtime. The local builder produces a signed catalog cache the client verifies
exactly like the hosted one; the publish wrapper (R2, cron) is added when going live.
"""

from __future__ import annotations
