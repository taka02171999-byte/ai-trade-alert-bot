# orchestrator.py — 夜の一括実行（optimize → report）
import importlib

def run(module_name):
    try:
        m = importlib.import_module(module_name)
        if hasattr(m, "main"):
            return m.main()
        return 0
    except Exception as e:
        print(f"[orchestrator] error in {module_name}: {e}")
        return 1

def main():
    rc1 = run("optimize_percent")
    rc2 = run("report_all")
    return 0 if (rc1 == 0 and rc2 == 0) else 1

if __name__ == "__main__":
    raise SystemExit(main())
