"""Scores index must stay pinned to theme detail payloads."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib import theme_score as scores


def _payload(slug: str, score: float, as_of: str = "2026-09-01") -> dict:
    return {
        "score_id": f"{slug}_id",
        "theme": slug.replace("-", " "),
        "theme_slug": slug,
        "label": f"{slug} backdrop",
        "pack": "test_pack",
        "score": score,
        "as_of": as_of,
        "history": [{"date": as_of, "value": score}],
    }


class ThemeScoreIndexTests(unittest.TestCase):
    def test_local_latest_rebuilds_from_theme_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            scores_dir = Path(tmp)
            # Stale index row for theme-a would have drifted without rebuild.
            (scores_dir / "themes").mkdir()
            (scores_dir / "themes" / "theme-a.json").write_text(
                json.dumps(_payload("theme-a", 40.0)), encoding="utf-8"
            )
            (scores_dir / "latest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 0,
                        "themes": [
                            {
                                "theme_slug": "theme-a",
                                "theme": "theme a",
                                "score": 99.0,
                                "as_of": "2026-01-01",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            _, local_index = scores.write_scores_local(
                [_payload("theme-b", 70.0)],
                scores_dir=scores_dir,
            )
            by_slug = {r["theme_slug"]: r for r in local_index["themes"]}
            self.assertEqual(by_slug["theme-a"]["score"], 40.0)
            self.assertEqual(by_slug["theme-b"]["score"], 70.0)

            disk = json.loads((scores_dir / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(
                {r["theme_slug"]: r["score"] for r in disk["themes"]},
                {"theme-a": 40.0, "theme-b": 70.0},
            )

    def test_publish_index_only_updates_scored_themes(self):
        remote = {
            "schema_version": 0,
            "themes": [
                {
                    "theme_slug": "theme-a",
                    "theme": "theme a",
                    "score": 73.5,
                    "as_of": "2026-09-01",
                },
                {
                    "theme_slug": "theme-b",
                    "theme": "theme b",
                    "score": 50.0,
                    "as_of": "2026-08-01",
                },
            ],
        }
        fresh = [_payload("theme-b", 58.0, as_of="2026-09-01")]
        publish_index = scores.merge_scores_index(remote, fresh)
        scores.assert_index_matches_theme_scores(publish_index, fresh)
        by_slug = {r["theme_slug"]: r for r in publish_index["themes"]}
        # Unscored remote row preserved (no stale-local poison).
        self.assertEqual(by_slug["theme-a"]["score"], 73.5)
        self.assertEqual(by_slug["theme-b"]["score"], 58.0)

    def test_assert_catches_drift(self):
        index = scores.scores_index([_payload("theme-a", 70.0)])
        index["themes"][0]["score"] = 99.0
        with self.assertRaises(AssertionError):
            scores.assert_index_matches_theme_scores(
                index, [_payload("theme-a", 70.0)]
            )


if __name__ == "__main__":
    unittest.main()
