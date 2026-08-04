import os
from notion_client import Client
from dotenv import load_dotenv

load_dotenv()

notion = Client(auth=os.environ.get("NOTION_TOKEN"))

SOURCES_DB_ID = os.environ.get("NOTION_SOURCES_DB_ID")
ITEMS_DB_ID = os.environ.get("NOTION_ITEMS_DB_ID")


def get_data_source_id(database_id: str) -> str:
    db = notion.databases.retrieve(database_id=database_id)
    return db["data_sources"][0]["id"]


SOURCES_DATA_SOURCE_ID = get_data_source_id(SOURCES_DB_ID)
ITEMS_DATA_SOURCE_ID = get_data_source_id(ITEMS_DB_ID)


def find_or_create_source(name: str) -> str:
    existing = notion.data_sources.query(
        data_source_id=SOURCES_DATA_SOURCE_ID,
        filter={
            "property": "Name",
            "title": {"equals": name}
        }
    )

    if existing["results"]:
        return existing["results"][0]["id"]

    new_source = notion.pages.create(
        parent={"type": "data_source_id", "data_source_id": SOURCES_DATA_SOURCE_ID},
        properties={
            "Name": {"title": [{"text": {"content": name}}]},
            "Category": {"select": {"name": "Other"}},
            "Status": {"select": {"name": "Active"}}
        }
    )
    return new_source["id"]


def create_item(
    title: str,
    source_page_id: str,
    category: str,
    event_date: str = None,
    priority: str = "Medium",
    location_or_link: str = "",
    source_email: str = "",
    attachment_summary: str = "",
    confidence_score: float = 0.0,
    notes: str = ""
):
    category_map = {
        "job_opportunity": "Job Opportunity",
        "meeting": "Meeting",
        "deadline": "Deadline",
        "irrelevant": "Other"
    }

    status = "Scheduled" if event_date else "Logged only"

    properties = {
        "Title": {"title": [{"text": {"content": title}}]},
        "source": {"relation": [{"id": source_page_id}]},
        "category": {"select": {"name": category_map.get(category, "Other")}},
        "status": {"select": {"name": status}},
        "priority": {"select": {"name": priority.capitalize()}},
        "location_or_link": {"rich_text": [{"text": {"content": location_or_link or ""}}]},
        "source_email": {"rich_text": [{"text": {"content": source_email or ""}}]},
        "attachment_summary": {"rich_text": [{"text": {"content": attachment_summary or ""}}]},
        "confidence_score": {"number": confidence_score},
        "notes": {"rich_text": [{"text": {"content": notes or ""}}]}
    }

    if event_date:
        properties["event_date"] = {"date": {"start": event_date}}

    new_item = notion.pages.create(
        parent={"type": "data_source_id", "data_source_id": ITEMS_DATA_SOURCE_ID},
        properties=properties
    )
    return new_item["id"]