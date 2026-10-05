# parity/api_targets.py —— API 目标的唯一映射（manifest 不再写 path）
from dataclasses import dataclass

@dataclass(frozen=True)
class ApiTarget:
    app_name: str
    method: str
    path: str

API_TARGETS = {
    "panel.session.status":    ApiTarget("panel", "GET", "/api/session"),
    "panel.db_health":         ApiTarget("panel", "GET", "/api/monitor/db-health"),
    "panel.db_aggregate":      ApiTarget("panel", "GET", "/api/monitor/db-health/aggregate"),
    "panel.media_queue":       ApiTarget("panel", "GET", "/api/media/queue"),
    "panel.terminal_events":   ApiTarget("panel", "GET", "/api/media/terminal-events"),
    "panel.web_scrapes.list":  ApiTarget("panel", "GET", "/api/web/scrapes"),
    "panel.web_scrape.detail": ApiTarget("panel", "GET", "/api/web/scrapes/{run_id}"),
    "panel.selector.history":  ApiTarget("panel", "GET", "/api/web/selectors/{domain}/{selector_id}"),
}
