"""Load test: 100 concurrent dashboard users.

    locust -f loadtest/locustfile.py --headless -u 100 -r 20 -t 3m --host http://localhost:8000 \
        --csv loadtest/results/run

Traffic mix mirrors the UI: overview polling, ward drill-downs, search, failure-impact
analysis and assistant questions. Full simulations (~0.3-1 s CPU each) are excluded:
they are an analyst action, not a per-user poll, and belong on a job queue at scale.
"""
from __future__ import annotations

import os
import random

from locust import HttpUser, between, constant, task

WARDS = ["MICU", "SICU", "CVICU", "CCU", "SDU", "CARD", "MEDA", "NEURO", "MEDB", "ONC", "SURG", "ORTHO", "TXP", "PSY", "ED"]
FAILURES = ["ZV-O2-MICU", "PNL-2CR", "CKT-MICU-01", "SW-3A", "UPS-3", "ATS-CR", "GEN-2", "AST-VENTILATOR-0003",
            "CKT-SURG-04", "ZV-O2-ONC", "PNL-5N", "AST-INFUSION-PUMP-0100", "AIR-COMP", "CORE-1"]
SEARCH = ["generator", "ventilator", "medical icu", "oxygen", "panel", "monitor", "dialysis", "switch"]
QUESTIONS = ["How many free ICU beds are there?", "What happens if the MICU oxygen zone valve fails?",
             "How many ventilators are available hospital-wide?", "How many CCRN nurses work in the SICU?",
             "Which ward has the highest occupancy?", "What does AST-VENTILATOR-0003 depend on?"]


class DashboardUser(HttpUser):
    # STRESS=1 removes think time to find the saturation point
    wait_time = constant(0) if os.getenv("STRESS") == "1" else between(1, 3)

    @task(6)
    def overview(self):
        self.client.get("/api/summary")
        self.client.get("/api/wards")

    @task(4)
    def ward(self):
        self.client.get(f"/api/wards/{random.choice(WARDS)}", name="/api/wards/[code]")

    @task(3)
    def search(self):
        self.client.get(f"/api/search?q={random.choice(SEARCH)}", name="/api/search")

    @task(3)
    def impact(self):
        self.client.get(f"/api/impact/{random.choice(FAILURES)}", name="/api/impact/[id]")

    @task(2)
    def beds(self):
        self.client.get("/api/beds/availability?unit_type=ICU", name="/api/beds/availability")

    @task(1)
    def assistant(self):
        self.client.post("/api/assistant/ask", json={"question": random.choice(QUESTIONS)}, name="/api/assistant/ask")

    @task(1)
    def explore(self):
        self.client.get("/api/nodes/WARD-MICU/neighborhood", name="/api/nodes/[id]/neighborhood")
