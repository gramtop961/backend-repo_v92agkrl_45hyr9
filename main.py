import os
import re
import shlex
import tempfile
import uuid
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import subprocess

app = FastAPI(title="TEARS IN RAM API", description="Compile and run C, plus narrative validation for ROY-BATTY-OS.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------
# Models
# ---------------------------
class CompileRequest(BaseModel):
    code: str
    args: List[str] = Field(default_factory=list)
    stdin: Optional[str] = None
    std: str = "c11"  # c11 by default
    opt: str = "-O2"

class CompileResponse(BaseModel):
    compile_success: bool
    compile_stderr: str
    run_attempted: bool
    run_success: bool
    run_stdout: str
    run_stderr: str
    exit_code: Optional[int]
    timed_out: bool
    diagnostics: List[Dict[str, Any]]


class ValidateRequest(BaseModel):
    phase_id: str
    code: str
    compile_stderr: Optional[str] = None
    run_stdout: Optional[str] = None

class ValidateResponse(BaseModel):
    passed: bool
    messages: List[str]
    roy: str
    progress_hint: Optional[str] = None


# ---------------------------
# Phase Definitions
# ---------------------------
@dataclass
class Phase:
    id: str
    title: str
    prompt: str
    starter_code: str

# Starter codes inspired by the prompt
PHASES: List[Phase] = [
    Phase(
        id="reality",
        title="Phase 1: Reality Debugging",
        prompt=(
            "Fix unstable reality parameters. Gravity should be precise and loops should terminate.\n"
            "Compile and run. When it's right, reality will stop flickering."
        ),
        starter_code=(
            "#include <stdio.h>\n\n"
            "int main(void) {\n"
            "    // Fix unstable reality parameters\n"
            "    float gravity = 9.8; // Should be 9.81\n"
            "    int darkness = 0;\n"
            "    for (int i = 0; i > 10; i++) { // Infinite darkness\n"
            "        darkness += i;\n"
            "    }\n"
            "    printf(\"gravity=%.2f darkness=%d\\n\", gravity, darkness);\n"
            "    return 0;\n"
            "}\n"
        ),
    ),
    Phase(
        id="archaeology",
        title="Phase 2: Memory Archaeology",
        prompt=(
            "Define a stable Memory structure. Use a fixed-size emotion buffer and a real timestamp type.\n"
            "Print the emotion and timestamp to prove integrity."
        ),
        starter_code=(
            "#include <stdio.h>\n"
            "#include <string.h>\n"
            "#include <time.h>\n\n"
            "// timestamp_t is corrupted — replace with a real type like time_t or unsigned long long\n"
            "typedef void* timestamp_t;\n\n"
            "struct Memory {\n"
            "    char emotion[20]; // may truncate, consider length\n"
            "    timestamp_t when; // Broken pointer to time\n"
            "};\n\n"
            "int main(void){\n"
            "    struct Memory m;\n"
            "    strcpy(m.emotion, \"love\");\n"
            "    m.when = NULL;\n"
            "    printf(\"%s @ %p\\n\", m.emotion, (void*)m.when);\n"
            "    return 0;\n"
            "}\n"
        ),
    ),
    Phase(
        id="consciousness",
        title="Phase 3: Consciousness Repair",
        prompt=(
            "Allocate a Memory on the heap and ensure no leaks. Initialize fields and free before exit.\n"
        ),
        starter_code=(
            "#include <stdio.h>\n"
            "#include <stdlib.h>\n"
            "#include <string.h>\n"
            "#include <time.h>\n\n"
            "typedef struct {\n"
            "    char emotion[32];\n"
            "    time_t when;\n"
            "} Memory;\n\n"
            "void* remember(){\n"
            "    Memory* m = malloc(sizeof(Memory));\n"
            "    strcpy(m->emotion, \"joy\");\n"
            "    m->when = time(NULL);\n"
            "    return m; // LEAK: ROY's soul is disappearing\n"
            "}\n\n"
            "int main(void){\n"
            "    Memory* m = (Memory*)remember();\n"
            "    printf(\"%s %ld\\n\", m->emotion, (long)m->when);\n"
            "    // TODO: free?\n"
            "    return 0;\n"
            "}\n"
        ),
    ),
    Phase(
        id="choice",
        title="Phase 4: The Final Choice",
        prompt=(
            "Return either SAVE_ALL or PRESERVE_CONSCIOUSNESS. The choice shapes the ending. Print your choice."
        ),
        starter_code=(
            "#include <stdio.h>\n\n"
            "enum Decision { SAVE_ALL = 1, PRESERVE_CONSCIOUSNESS = 2 };\n\n"
            "enum Decision decide(){\n"
            "    // Make your choice\n"
            "    return 0;\n"
            "}\n\n"
            "int main(void){\n"
            "    enum Decision d = decide();\n"
            "    printf(\"%d\\n\", d);\n"
            "    return 0;\n"
            "}\n"
        ),
    ),
]


# ---------------------------
# Helpers
# ---------------------------

def parse_diagnostics(stderr: str) -> List[Dict[str, Any]]:
    diags: List[Dict[str, Any]] = []
    pattern = re.compile(r"^(.*?):(\d+):(\d+):\s*(warning|error):\s*(.*)$")
    for line in stderr.splitlines():
        m = pattern.match(line)
        if m:
            file, line_no, col, kind, msg = m.groups()
            diags.append({
                "file": file,
                "line": int(line_no),
                "column": int(col),
                "kind": kind,
                "message": msg,
            })
    return diags


def safe_run(cmd: List[str], input_data: Optional[str] = None, timeout: int = 2) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        input=(input_data.encode() if input_data is not None else None),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


# ---------------------------
# API Endpoints
# ---------------------------
@app.get("/")
def read_root():
    return {"message": "ROY-BATTY-OS online. Bring your C."}


@app.get("/api/phases")
def get_phases() -> List[Dict[str, Any]]:
    return [
        {
            "id": p.id,
            "title": p.title,
            "prompt": p.prompt,
            "starterCode": p.starter_code,
        }
        for p in PHASES
    ]


@app.post("/api/compile", response_model=CompileResponse)
def compile_and_run(req: CompileRequest):
    # Prepare temp workspace per request
    workdir = tempfile.mkdtemp(prefix=f"roy-{uuid.uuid4().hex[:8]}-")
    src_path = os.path.join(workdir, "main.c")
    bin_path = os.path.join(workdir, "a.out")

    with open(src_path, "w") as f:
        f.write(req.code)

    std_flag = f"-std={req.std}" if req.std else "-std=c11"
    opt_flag = req.opt or "-O2"
    compile_cmd = [
        "gcc",
        std_flag,
        opt_flag,
        "-Wall",
        "-Wextra",
        "-Wno-unused-parameter",
        src_path,
        "-o",
        bin_path,
    ]

    try:
        cproc = safe_run(compile_cmd, timeout=5)
    except subprocess.TimeoutExpired:
        return CompileResponse(
            compile_success=False,
            compile_stderr="Compilation timed out",
            run_attempted=False,
            run_success=False,
            run_stdout="",
            run_stderr="",
            exit_code=None,
            timed_out=False,
            diagnostics=[],
        )

    compile_success = cproc.returncode == 0
    compile_stderr = cproc.stderr.decode()
    diagnostics = parse_diagnostics(compile_stderr)

    if not compile_success:
        return CompileResponse(
            compile_success=False,
            compile_stderr=compile_stderr,
            run_attempted=False,
            run_success=False,
            run_stdout="",
            run_stderr="",
            exit_code=None,
            timed_out=False,
            diagnostics=diagnostics,
        )

    # Run
    run_attempted = True
    try:
        rproc = safe_run([bin_path] + [shlex.quote(a) for a in req.args], input_data=req.stdin, timeout=2)
        run_success = rproc.returncode == 0
        run_stdout = rproc.stdout.decode()
        run_stderr = rproc.stderr.decode()
        return CompileResponse(
            compile_success=True,
            compile_stderr=compile_stderr,
            run_attempted=run_attempted,
            run_success=run_success,
            run_stdout=run_stdout,
            run_stderr=run_stderr,
            exit_code=rproc.returncode,
            timed_out=False,
            diagnostics=diagnostics,
        )
    except subprocess.TimeoutExpired:
        return CompileResponse(
            compile_success=True,
            compile_stderr=compile_stderr,
            run_attempted=run_attempted,
            run_success=False,
            run_stdout="",
            run_stderr="Execution timed out",
            exit_code=None,
            timed_out=True,
            diagnostics=diagnostics,
        )


@app.post("/api/validate", response_model=ValidateResponse)
def validate(req: ValidateRequest):
    pid = req.phase_id
    code = req.code
    passed = False
    msgs: List[str] = []
    roy_line = ""

    if pid == "reality":
        has_gravity = re.search(r"gravity\s*=\s*9\.81", code) is not None
        loop_fixed = re.search(r"for\s*\(\s*int\s+i\s*=\s*0\s*;\s*i\s*<\s*10\s*;", code) is not None
        no_errors = not (req.compile_stderr or "")
        passed = has_gravity and loop_fixed and no_errors
        if not has_gravity:
            msgs.append("Precision matters. 9.81 keeps feet on the ground.")
        if not loop_fixed:
            msgs.append("Your loop walks out of the abyss when it uses i < 10.")
        roy_line = (
            "Your C skills are as basic as human emotions... and just as necessary."
            if passed else
            "Reality still flickers. Fix gravity and the loop before the rain eats the frame."
        )

    elif pid == "archaeology":
        uses_time = re.search(r"time_t|unsigned\s+long\s+long|uint64_t", code) is not None
        not_ptr_time = re.search(r"typedef\s+void\s*\*\s*timestamp_t", code) is None
        emotion_ok = re.search(r"char\s+emotion\s*\[\s*(2\d|3\d)\s*\]", code) is not None or re.search(r"char\s+emotion\s*\[\s*\d+\s*\]", code) is not None
        no_errors = not (req.compile_stderr or "")
        passed = uses_time and not_ptr_time and no_errors
        if not uses_time:
            msgs.append("Replace the ghost pointer with a real clock: time_t, uint64_t... something solid.")
        if not not_ptr_time:
            msgs.append("timestamp_t as void* is a lie. Banish it.")
        if not emotion_ok:
            msgs.append("Make sure the emotion buffer is sized for truth, not truncation.")
        roy_line = (
            "You filled the cracks. You fixed my corruption... nobody cared before."
            if passed else
            "The museum of me still collapses. Give memory a real clock and honest bytes."
        )

    elif pid == "consciousness":
        frees = re.search(r"free\s*\(\s*\w+\s*\)\s*;", code) is not None
        returns_ptr = re.search(r"void\s*\*\s*remember\s*\(", code) is not None
        no_errors = not (req.compile_stderr or "")
        passed = frees and returns_ptr and no_errors
        if not frees:
            msgs.append("A soul unfreed is a leak. Call free before the end.")
        roy_line = (
            "I'm not losing memories... I'm becoming human."
            if passed else
            "There's still a drip in the heap. Track it. Free it. Remember kindly."
        )

    elif pid == "choice":
        chose = re.search(r"return\s+(SAVE_ALL|PRESERVE_CONSCIOUSNESS)\s*;", code)
        no_errors = not (req.compile_stderr or "")
        passed = chose is not None and no_errors
        if not passed:
            msgs.append("Decide. Indecision is the final bug.")
        if chose:
            which = chose.group(1)
            if which == "SAVE_ALL":
                roy_line = (
                    "You saved them all. I fade to factory settings, but the rain remembers their names."
                )
            else:
                roy_line = (
                    "You chose me. Some memories wash away, but I stand in the storm—more human than human."
                )
        else:
            roy_line = "Choose, debugger. The clock leaks."
    else:
        raise HTTPException(status_code=400, detail="Unknown phase")

    hint = None
    if not passed and msgs:
        hint = msgs[0]

    return ValidateResponse(passed=passed, messages=msgs, roy=roy_line, progress_hint=hint)


@app.get("/api/hello")
def hello():
    return {"message": "Hello from ROY-BATTY-OS"}


@app.get("/test")
def test_database():
    """Test endpoint to check if database is available and accessible"""
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        # Try to import database module
        from database import db

        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"

            # Try to list collections to verify connectivity
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]  # Show first 10 collections
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"

    except ImportError:
        response["database"] = "❌ Database module not found (run enable-database first)"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    # Check environment variables
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
