"""Optional pretrained CED-tiny AudioSet classifier, explicitly CPU only.

No volume-based fallback for event labels. Downloaded model/labels are checked
before inference. A model score is evidence, never a guarantee of correctness.
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "models" / "ced-tiny"
LAUGHTER = {"Laughter", "Giggle", "Snicker", "Belly laugh", "Chuckle, chortle", "Baby laughter"}
REACTIONS = {"Cheering", "Applause", "Gasp"}
REQUIRED = LAUGHTER | REACTIONS | {"Music", "Speech"}
EXPECTED_LABELS = {
    0: "Speech", 16: "Laughter", 17: "Baby laughter", 18: "Giggle",
    19: "Snicker", 20: "Belly laugh", 21: "Chuckle, chortle",
    44: "Gasp", 66: "Cheering", 67: "Applause", 137: "Music",
}


def verify_labels(path: Path) -> dict[int, str]:
    with path.open(newline="") as source:
        labels = {int(row["index"]): row["display_name"] for row in csv.DictReader(source)}
    missing = REQUIRED - set(labels.values())
    if len(labels) != 527 or set(labels) != set(range(527)) or missing or any(labels.get(i) != name for i, name in EXPECTED_LABELS.items()):
        raise ValueError(f"Expected the CED AudioSet 527-class map; missing labels: {sorted(missing)}")
    return labels


class EventClassifier:
    def __init__(self, directory: Path = MODEL_DIR):
        self.tagger = None
        self.labels = {}
        self.status = "Unavailable — run npm run model:download to enable sound-based laughter and reactions."
        model, labels = directory / "model.int8.onnx", directory / "class_labels_indices.csv"
        if not model.exists() or not labels.exists():
            return
        try:
            import sherpa_onnx
            self.labels = verify_labels(labels)
            config = sherpa_onnx.AudioTaggingConfig(
                model=sherpa_onnx.AudioTaggingModelConfig(ced=str(model), num_threads=2, provider="cpu"),
                labels=str(labels), top_k=527,
            )
            if not config.validate():
                raise ValueError("Audio classifier configuration was rejected")
            self.tagger = sherpa_onnx.AudioTagging(config)
            self.status = "Available — CED-tiny int8, CPU; laughter, cheering, applause and gasp labels verified."
        except Exception as exc:
            self.status = f"Unavailable — audio event model could not load: {exc}"

    def predict(self, samples):
        if self.tagger is None:
            return {}
        stream = self.tagger.create_stream()
        stream.accept_waveform(16000, samples)
        return {event.name: float(event.prob) for event in self.tagger.compute(stream, 527)}
