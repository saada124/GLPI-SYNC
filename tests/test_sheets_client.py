from unittest.mock import patch
import json
import pytest
from sheets_client import SheetsClient


@pytest.fixture
def client():
    return SheetsClient("https://example.com/webhook", "test-token")


class TestUpdateRow:
    def test_filters_empty_values(self, client):
        with patch.object(client, "_call") as mock_call:
            client.update_row("Tickets", 5, {"Name": "Hello", "Empty": "", "Null": None, "Zero": 0})
            mock_call.assert_called_once_with(
                "updateRow", sheet="Tickets", row="5",
                values=json.dumps({"Name": "Hello"})
            )

    def test_filters_zero(self, client):
        with patch.object(client, "_call") as mock_call:
            client.update_row("Assets", 3, {"Count": 0})
            mock_call.assert_not_called()

    def test_skip_when_all_empty(self, client):
        with patch.object(client, "_call") as mock_call:
            client.update_row("Tickets", 5, {"Empty": "", "Null": None})
            mock_call.assert_not_called()


class TestBatchUpdateRows:
    def test_filters_empty_values(self, client):
        with patch.object(client, "_call") as mock_call:
            updates = [(5, {"Name": "Hello", "Empty": ""}),
                       (6, {"Name": "World", "Zero": 0})]
            client.batch_update_rows("Tickets", updates)
            mock_call.assert_called_once_with(
                "batchUpdateRows", sheet="Tickets",
                rows=json.dumps([{"row": 5, "values": {"Name": "Hello"}},
                                 {"row": 6, "values": {"Name": "World"}}])
            )

    def test_skips_all_empty(self, client):
        with patch.object(client, "_call") as mock_call:
            client.batch_update_rows("Tickets", [(5, {"Empty": "", "Zero": 0})])
            mock_call.assert_not_called()

    def test_fallback_on_failure(self, client):
        with patch.object(client, "_call") as mock_call:
            mock_call.side_effect = [RuntimeError("batch failed"), None, None]
            updates = [(5, {"Name": "Hello"}), (6, {"Name": "World"})]
            client.batch_update_rows("Tickets", updates)
            assert mock_call.call_count == 3
            mock_call.assert_any_call(
                "batchUpdateRows", sheet="Tickets",
                rows=json.dumps([{"row": 5, "values": {"Name": "Hello"}},
                                 {"row": 6, "values": {"Name": "World"}}])
            )
            mock_call.assert_any_call(
                "updateRow", sheet="Tickets", row="5",
                values=json.dumps({"Name": "Hello"})
            )
            mock_call.assert_any_call(
                "updateRow", sheet="Tickets", row="6",
                values=json.dumps({"Name": "World"})
            )

    def test_fallback_called_with_filtered_data(self, client):
        with patch.object(client, "_call") as mock_call:
            mock_call.side_effect = [RuntimeError("batch failed"), None]
            client.batch_update_rows("Tickets", [(5, {"Name": "Hello", "Empty": ""})])
            assert mock_call.call_count == 2
            mock_call.assert_any_call(
                "updateRow", sheet="Tickets", row="5",
                values=json.dumps({"Name": "Hello"})
            )
