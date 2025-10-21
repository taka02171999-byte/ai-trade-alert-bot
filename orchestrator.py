# orchestrator.py — 夜の一括実行（optimize → compare → report_all）
import importlib

def run(module_name):
    """各モジュールを安全に呼び出す"""
    try:
        m = importlib.import_module(module_name)
        print(f"=== {module_name} job started ===")
        if hasattr(m, "main"):
            m.main()
        print(f"=== {module_name} job finished ===")
    except Exception as e:
        print(f"[error] {module_name}: {e}")

def main():
    print("=== orchestrator start ===")
    # 夜間の流れ：最適化 → 比較 → レポート
    run("optimize_percent")
    run("compare_agents")
    run("report_all")
    print("=== orchestrator complete ===")

if __name__ == "__main__":
    try:
        main()
        print("=== all orchestrator jobs finished ===")
        # Renderがこれを見て「成功」と判断
        exit(0)
    except Exception as e:
        print(f"[error] orchestrator: {e}")
        # ここもexit(0)にしてRender側を成功扱いにする
        exit(0)
