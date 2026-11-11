# The "translate" batch job: titles and summaries of items in languages outside read_langs,
# into TARGET_LANG, with the engine chosen per source language in config "translate_engine".
from ..config import TARGET_LANG, worker_threads
from ..jobs import Job
from ..store import MAX_ATTEMPTS
from ..text import canonical
from . import registry

MAX_GROWTH = 5     # output longer than five times the input counts as a failure


def check_output(source_text, output):
    """Returns (text, error). Engine output is plain text: canonical(), not normalize(), so a
    legitimate '<' in a headline survives."""
    text = canonical(output or "")
    if not text:
        return "", "empty output"
    if len(text) > MAX_GROWTH * max(len(source_text or ""), 1):
        return "", "output more than five times longer than the input"
    return text, ""


class TranslateJob(Job):
    name = "translate"

    def __init__(self, cfg, models_dir):
        super().__init__()
        self.read_langs = list(cfg["read_langs"])
        self.mapping = dict(cfg["translate_engine"])
        self.batch_size = cfg["translate_batch"]
        self.threads = worker_threads(cfg)
        self.models_dir = models_dir
        self.target = TARGET_LANG
        self.engines = {}

    def engine_name(self, src):
        return self.mapping.get(src) or self.mapping["default"]

    def available(self):
        problems = []
        for name in sorted(set(self.mapping.values())):
            reason = registry.check(name, self.models_dir)
            if reason:
                problems.append("%s: %s" % (name, reason))
        self.reason = "; ".join(problems)
        return not problems

    def engine(self, name):
        # Loading a model can fail (corrupt file, out of memory). That raises out of run(), so the
        # worker reports the job as failing instead of charging a failure to every item.
        if name not in self.engines:
            self.engines[name] = registry.create(name, self.models_dir, self.threads)
        return self.engines[name]

    def pending(self, store, limit):
        return store.pending_translations(self.read_langs, self.target, limit, MAX_ATTEMPTS)

    def result(self, unit, engine, text, error):
        return {"item_id": unit["item_id"], "field": unit["field"], "lang": self.target,
                "text": text, "engine": engine, "error": error}

    def run(self, batch):
        groups = {}
        for unit in batch:
            groups.setdefault((unit["lang"], unit["field"]), []).append(unit)
        results = []
        for (src, field) in sorted(groups):
            units = groups[(src, field)]
            name = self.engine_name(src)
            engine = self.engine(name)
            try:
                if not engine.supports(src, self.target):
                    raise ValueError("%s does not translate %s into %s" % (name, src, self.target))
                outputs = engine.translate([u["text"] for u in units], src, self.target)
                if len(outputs) != len(units):
                    raise ValueError("%s returned %d texts for %d inputs" % (name, len(outputs), len(units)))
            except Exception as e:
                error = ("%s: %s" % (type(e).__name__, e))[:300]
                results.extend(self.result(u, name, "", error) for u in units)
                continue
            for unit, output in zip(units, outputs):
                text, error = check_output(unit["text"], output)
                results.append(self.result(unit, name, text, error))
        return results

    def save(self, store, batch, results):
        return store.save_translations(results)
