# SPDX-License-Identifier: Apache-2.0
"""The only http(s) URLs a report is allowed to mention as links.

Both are plain anchors. The HTML does not load them. The SVG namespace is not
a fetch either; browsers do not retrieve ``xmlns``.
"""

PROJECT_URL = "https://github.com/tracetally/tracetally"
EVALS_URL = "https://github.com/tracetally/tracetally#validate-with-evals"
SVG_NAMESPACE = "http://www.w3.org/2000/svg"

ALLOWED_HREFS = frozenset({PROJECT_URL, EVALS_URL})
