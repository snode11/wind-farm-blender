"""Build an isolated DISCON with working native pitch in yaw-only operation.

The upstream source and installed training controller are left intact. Each
build records the source, patch and binary hashes for the replay provenance.
"""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def build(output, rotor_target_rpm=12.1):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    source = ROOT/'wfcrl-env/wfcrl/simulators/fastfarm/src/DISCON/DISCON.F90'
    original = source.read_text()
    original = original.replace('PC_MaxRat     =       0.01745329',
                                'PC_MaxRat     =       0.1396263')
    if not 9 <= rotor_target_rpm <= 12.1:
        raise ValueError('Rotor target must be between 9 and 12.1 rpm')
    if rotor_target_rpm != 12.1:
        import math
        speed=rotor_target_rpm*97*2*math.pi/60
        original=original.replace('PC_RefSpd     =     122.9096',f'PC_RefSpd     =     {speed:.7f}')
        original=original.replace('VS_RtGnSp     =     121.6805',f'VS_RtGnSp     =     {speed*.99:.7f}')
        original=original.replace('VS_RtPwr      = 5296610.0',f'VS_RtPwr      = {5296610*(rotor_target_rpm/12.1)**3:.3f}')
    original=original.replace('avrSWAP(48) = 1*(YawRef - YawAngle)!yaw rate',
        'avrSWAP(48) = MIN(MAX(YawRef-YawAngle,-0.005235988),0.005235988) ! 0.3 deg/s')
    begin = original.index('          IF (PitchExternalCommand) THEN')
    end = original.index('      LastTimePC = Time', begin)
    block = original[begin:end]
    old = '            ENDIF'
    if block.count(old) != 1:
        raise ValueError('Native-pitch patch no longer matches upstream source')
    block = block.replace(old, '''            ELSE
                ! Native collective pitch must run when MAPPO controls yaw only.
                DO K = 1,NumBl
                    PitRate(K) = (PitComT - PitCom(K))/ElapTime
                    PitRate(K) = MIN(MAX(PitRate(K),-PC_MaxRat),PC_MaxRat)
                    PitCom(K) = PitCom(K) + PitRate(K)*ElapTime
                    LastFiltPitCom(K) = PitCom(K)
                ENDDO
            ENDIF''')
    patched = output/'DISCON.F90'
    patched.write_text(original[:begin]+block+original[end:])
    dll = output/'DISCON.dll'
    command = ['gfortran', '-dynamiclib', '-ffree-line-length-none',
               '-fdefault-real-8', '-fcheck=all', '-DIMPLICIT_DLLEXPORT',
               '-O3', '-fPIC', str(patched.resolve()), '-o', str(dll.resolve())]
    subprocess.run(command, check=True, cwd=output)
    metadata = dict(source=str(source.relative_to(ROOT)),
                    source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    patched_sha256=hashlib.sha256(patched.read_bytes()).hexdigest(),
                    binary_sha256=hashlib.sha256(dll.read_bytes()).hexdigest(),
                    command=command,rotor_target_rpm=rotor_target_rpm,
                    change='Restore native pitch; pitch command slew 8 deg/s; yaw command rate 0.3 deg/s; explicit rotor target')
    (output/'build.json').write_text(json.dumps(metadata, indent=2))
    return dll


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--rotor-target-rpm',type=float,default=12.1)
    args=parser.parse_args()
    print(build(args.output,args.rotor_target_rpm))
