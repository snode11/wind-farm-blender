"""Test replay functionality imports"""
import sys
import os

# Ensure we're in the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from wfrl.studio.trainer import Trainer
    from wfrl.scene.schema import load_scene
    print("[OK] Imports successful")

    # Test loading checkpoint and scene
    scene = load_scene("scenes/turb3_stagger.yaml")
    print(f"[OK] Scene loaded: {scene.name}, {scene.n} turbines")

    # Test Trainer initialization (replay mode)
    trainer = Trainer(scene, replay=True, replay_steps=10,
                      ckpt_path="results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref.pt")
    print(f"[OK] Trainer initialized (replay={trainer.replay}, replay_steps={trainer.replay_steps})")
    print("\nAll imports and initialization tests passed! Ready to run replay_studio.ps1")

except Exception as e:
    import traceback
    print(f"[ERROR] Failed:\n{traceback.format_exc()}")
    sys.exit(1)
