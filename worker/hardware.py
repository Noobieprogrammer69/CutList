"""Conservative local acceleration selection. Device presence is not a speed claim."""
import os
from pathlib import Path


def report():
    devices = list(Path("/dev/dri").glob("renderD*")) + list(Path("/dev").glob("nvidia[0-9]*"))
    usable = [str(p) for p in devices if os.access(p, os.R_OK | os.W_OK)]
    mode = os.environ.get("CUTLIST_ACCELERATION", "auto")
    if mode not in {"cpu", "auto", "gpu"}:
        mode = "cpu"
    return {"mode": mode, "cpu_threads": os.cpu_count() or 1, "gpu_devices": usable,
            "gpu_available": bool(usable), "selected": "gpu_requested" if mode == "gpu" or mode == "auto" and usable else "cpu",
            "message": "Local model GPU offload requested; actual use depends on Ollama support and speed must be benchmarked." if mode == "gpu" or mode == "auto" and usable else "CPU selected. No accessible GPU device found." if mode != "cpu" else "CPU selected explicitly.",
            "speech": "Parakeet CPU", "export": "H.264 CPU; original source resolution retained"}


def model_options():
    options = {"temperature": 0, "num_ctx": 4096, "num_thread": min(4, os.cpu_count() or 2)}
    if report()["selected"] == "cpu":
        options["num_gpu"] = 0
    # Omit num_gpu when offloading: Ollama selects layers that fit the device.
    return options
