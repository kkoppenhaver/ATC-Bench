"""Verbalizer cache + backend seam (DESIGN §8.1)."""

from __future__ import annotations

from atcbench.verbalizer import CachedVerbalizer, ResponseCache, TemplateVerbalizer, cache_key


class CountingBackend:
    def __init__(self):
        self.calls = 0

    def render(self, intent: dict) -> str:
        self.calls += 1
        return f"rendered:{intent['acid']}"


def test_cache_hit_avoids_backend():
    backend = CountingBackend()
    vb = CachedVerbalizer(backend, ResponseCache())
    intent = {"kind": "check_in", "acid": "AAL2452", "persona": "airline_crisp"}
    a = vb.render(intent)
    b = vb.render(intent)
    assert a == b == "rendered:AAL2452"
    assert backend.calls == 1  # second call served from cache
    assert vb.cache.hits == 1 and vb.cache.misses == 1


def test_cache_matches_template_output():
    template = TemplateVerbalizer()
    vb = CachedVerbalizer(TemplateVerbalizer(), ResponseCache())
    intent = {"kind": "check_in", "acid": "UAL881", "persona": "student_pilot",
              "destination_name": "Detroit"}
    assert vb.render(intent) == template.render(intent)


def test_cache_roundtrip(tmp_path):
    cache = ResponseCache()
    intent = {"kind": "say_again", "acid": "SWA334", "persona": "ga_relaxed"}
    cache.put(intent, "Say again for Southwest 334?")
    p = tmp_path / "vc.json"
    cache.save(p)
    loaded = ResponseCache.load(p)
    assert loaded.get(intent) == "Say again for Southwest 334?"


def test_key_changes_with_prompt_version():
    intent = {"kind": "check_in", "acid": "AAL1"}
    assert cache_key(intent, "v1") != cache_key(intent, "v2")
