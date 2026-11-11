"""Server-owned execution contract, shared by admission and the worker."""

from dataclasses import asdict, dataclass

MODULES = (
    "neo-bot-producer",
    "neo-beo-producer",
    "neo-bsdd-producer",
    "neo-ifcowl-producer",
    "neo-turtle-serializer",
    "neo-file-export",
)
OPTIONS = (
    "neo-ifcowl-producer.mode=full",
    "neo-bsdd-producer.opm_level=l2",
    "neo-bsdd-producer.compact=false",
    "neo-bsdd-producer.dedup_properties=false",
    "neo-turtle-serializer.layout=separate",
    "neo-turtle-serializer.grouping=sorted",
)
BASE_URI = "https://firebim.org/neo-source/"
PRODUCERS = ("bot", "beo", "bsdd", "ifcowl")
CHILD_ENVIRONMENT = {
    "LANG": "C.UTF-8",
    "RAYON_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "TOKIO_WORKER_THREADS": "2",
    "MALLOC_ARENA_MAX": "2",
}
TASK_SOFT_SECONDS = 240
TASK_HARD_SECONDS = 270
STORAGE_TIMEOUTS = {"connect_timeout": 3, "read_timeout": 5, "total_max_attempts": 2}


@dataclass(frozen=True)
class ProcessingLimits:
    conversion_seconds: int = 30
    mapping_seconds: int = 30
    validation_seconds: int = 30
    cpu_seconds: int = 30
    memory_bytes: int = 512 * 1024 * 1024
    file_bytes: int = 16 * 1024 * 1024
    disk_bytes: int = 256 * 1024 * 1024
    files: int = 20
    triples: int = 250_000

    def configuration(self):
        return asdict(self)


LIMITS = ProcessingLimits()


def worker_contract():
    return {
        "version": "neo-worker/1",
        "checkpoint_version": "neo-checkpoint/1",
        "limits": LIMITS.configuration(),
        "modules": list(MODULES),
        "options": list(OPTIONS),
        "base_uri": BASE_URI,
        "sandbox": "linux-rlimit-process-group/1",
        "supervisor_pid_binding": True,
        "environment": CHILD_ENVIRONMENT,
        "task_soft_seconds": TASK_SOFT_SECONDS,
        "task_hard_seconds": TASK_HARD_SECONDS,
        "storage": STORAGE_TIMEOUTS,
        "destination_rdf": "neo-destination-rdf/1",
        "publication": "neo-publication/1",
        "display_index": "neo-display-index/1",
        "mapping_runtime": "python3.12-rdflib7.6/1",
    }


def converter_arguments(binary):
    command = [str(binary), "source.ifc", "--output", "source.ttl", "--base-uri", BASE_URI]
    for module in MODULES:
        command.extend(["--module", module])
    for option in OPTIONS:
        command.extend(["--module-opt", option])
    return command
