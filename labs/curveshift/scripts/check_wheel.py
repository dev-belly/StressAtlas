"""Build and exercise a noneditable wheel in a clean environment outside the checkout."""

import os
import subprocess
import tempfile
import venv
from pathlib import Path

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    target = Path(directory).resolve()
    wheels = target / "wheels"
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(wheels), str(root)], check=True)
    environment = target / "environment"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    wheel = list(wheels.glob("curveshift_lab-*.whl"))
    assert len(wheel) == 1
    subprocess.run([str(python), "-m", "pip", "install", str(wheel[0])], check=True, cwd=target)
    subprocess.run([str(python), "-c", "import curveshift; print(curveshift.__file__)"], check=True, cwd=target)
    subprocess.run([str(python), "-m", "curveshift.cli", "report", "--out", "report"], check=True, cwd=target)
    subprocess.run([str(python), "-m", "curveshift.cli", "verify", "report"], check=True, cwd=target)
    subprocess.run([str(python), "-m", "curveshift.cli", "hedge-report", "--out", "hedge"], check=True, cwd=target)
    subprocess.run([str(python), "-m", "curveshift.cli", "hedge-verify", "hedge"], check=True, cwd=target)
print("Noneditable CurveShift wheel replayed outside the checkout")
