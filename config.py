PROPERTIES = {
    "windmill": {
        "utilities": ["enbridge", "peel-water", "alectra"],
        "cost_split": {"landlord_pct": 30, "tenant_pct": 70},
        "credentials_secret": "personal-ai/creds/windmill",
    },
    "bellcrest": {
        "utilities": ["enbridge", "peel-water", "alectra", "enercare"],
        "cost_split": {"landlord_pct": 67, "tenant_pct": 33},
        "credentials_secret": "personal-ai/creds/bellcrest",
    },
}
