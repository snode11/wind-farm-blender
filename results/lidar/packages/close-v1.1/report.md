# FAST.Farm 理想测距简化算法误差 — close

真实36秒单机FAST.Farm，固定9rpm、0度变桨、12.0m/s有剪切稳态风。无策略checkpoint；ElastoDyn两阶挥舞与一阶摆振启用，塔筒刚性。剔除0–18秒启动段，完整保留18–36秒。正常/较小净空按预设8/12m/s工况选定，不按误差选择。

预期测量区：叶片方位距正下方不超过3度，固定80Hz网格。B2有效33/71=46.478873%；经过8次，完全漏测0次。MAE=0.105805403m，最大绝对误差=0.148511080m，P95=0.148231108m，最大正偏差=0.076849575m。完整逐束及累计统计见数据包。

叶尖参考点为外端AeroDyn截面周界坐标均值；真值为该点到同高度刚性圆锥塔截面的最短距离，不是整片叶片全局碰撞距离。原生VTP表面来自同一求解时刻的位置、截面姿态和翼型坐标。前端不显示弹性变形。

每个保存时刻三叶片均通过保守三角面/塔筒半空间分离证书，共4323份；这是80Hz离散状态检查，不是连续时域碰撞证明。三束首交同时检查三叶片、圆锥塔筒和地面。估计器仅输入斜距与固定标定，不接触真值或实时叶尖。

解析和分开时空细化证据：validation-close.json；实测数值差异0.041557668m。此为有限两级敏感性检查，不能视为严格误差上界或现场精度。解析与比较证据完整性已检查；未指定精度容差，收敛验收状态为 NOT_ASSESSED_NO_TOLERANCE。B2误差包括手册直叶片假设、命中截面与叶尖差异、标定和几何离散，不能全部归因于弯曲。

资料核验：已逐图核对用户提供的 V3.0 原件（SHA256 1a126ba6420f47136b17986064908215d6b0d7e0d574ff56247c1255b07a0363）；图2-5与图3-26的X/Y命名不同，按物理主轴偏距映射。此核验不表示硬件精度或完整厂家算法验收。资料：MolasCL手册V3.0第3.5.3节（印刷第33页/PDF第34页、图3-26）及图2-5（印刷第8页/PDF第9页）；[OpenFAST表面生成说明](https://openfast.readthedocs.io/en/dev/_downloads/5f2ddf006568adc9b88d8118dc3f1732/FAST8_README.pdf)。

演示阈值7.0m在两预设工况真值区间计算后选定，滞回0.1m，仅用于演示，不用于保护或安全判断。

逐束统计（P95采用nearest rank，与包内统计一致）：

|光束|有效/预期|MAE(m)|MaxAbs(m)|P95(m)|最大正偏差(m)|完全漏测经过|
|---|---|---|---|---|---|---|
|B1|0/71|--|--|--|--|8|
|B2|33/71|0.105805403|0.148511080|0.148231108|0.076849575|0|
|B3|54/71|0.636737145|0.848690813|0.833217392|0.000000000|0|

完整验证实数（同包可追溯）：
```json
{
  "run_id": "close-v1",
  "analytic": {
    "analytic_truth_error_m": 0.0,
    "rigid_formula_error_m": 8.881784197001252e-16,
    "circle_polygon_convergence": [
      {
        "vertices": 32,
        "distance_m": 2.0096674859252937,
        "error_m": 0.00966748592529365
      },
      {
        "vertices": 64,
        "distance_m": 2.003541670784362,
        "error_m": 0.003541670784362072
      },
      {
        "vertices": 128,
        "distance_m": 2.0001079379875284,
        "error_m": 0.00010793798752839479
      },
      {
        "vertices": 256,
        "distance_m": 2.000106637131077,
        "error_m": 0.00010663713107694761
      }
    ]
  },
  "spatial": {
    "kind": "span_only_19_to_37",
    "base_run": "close-v1",
    "refined_run": "close-spatial-v1",
    "paired_samples": 71,
    "max_truth_difference_m": 0.04155766831831187,
    "mean_truth_difference_m": 0.039107313677331265,
    "max_paired_slant_difference_m": 0.14521823550051494,
    "baseline_dt_s": 0.00625,
    "refined_dt_s": 0.00625,
    "baseline_fps": 80,
    "refined_fps": 80,
    "baseline_span_sections": 19,
    "refined_span_sections": 37
  },
  "temporal": {
    "kind": "integration_and_output_timestep_halved",
    "base_run": "close-spatial-v1",
    "refined_run": "close-refined-v1",
    "paired_samples": 71,
    "max_truth_difference_m": 5.903590725164776e-05,
    "mean_truth_difference_m": 2.3639924919089074e-05,
    "max_paired_slant_difference_m": 0.00023367230789972382,
    "baseline_dt_s": 0.00625,
    "refined_dt_s": 0.003125,
    "baseline_fps": 80,
    "refined_fps": 160,
    "baseline_span_sections": 37,
    "refined_span_sections": 37
  },
  "truth_numerical_error_m": 0.04155766831831187,
  "interpretation": "Empirical two-level differences at matched evaluation times; not a rigorous upper uncertainty bound. Separate full-grid temporal sampling evidence is required.",
  "sampling": {
    "time_range_s": [
      18.0,
      36.0
    ],
    "base": {
      "expected_samples": 71,
      "valid_samples": 33,
      "valid_ratio": 0.4647887323943662,
      "mae_m": 0.08540360342523895,
      "max_abs_error_m": 0.12101074746146345,
      "p95_abs_error_m": 0.12068372696776031,
      "max_positive_bias_m": 0.09706231308133795,
      "passage_count": 8,
      "missed_passage_count": 0,
      "sample_interval_s": 0.0125,
      "expected_duration_s": 0.8875,
      "valid_duration_s": 0.4125,
      "passages": {
        "b1-p3": {
          "expected_samples": 9,
          "valid_samples": 4,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 23.3125,
          "last_valid_time_s": 23.35
        },
        "b1-p4": {
          "expected_samples": 9,
          "valid_samples": 5,
          "valid_ratio": 0.5555555555555556,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.0625,
          "missed": false,
          "first_valid_time_s": 29.975,
          "last_valid_time_s": 30.025
        },
        "b2-p3": {
          "expected_samples": 9,
          "valid_samples": 4,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 21.0875,
          "last_valid_time_s": 21.125
        },
        "b2-p4": {
          "expected_samples": 9,
          "valid_samples": 4,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 27.7625,
          "last_valid_time_s": 27.8
        },
        "b2-p5": {
          "expected_samples": 8,
          "valid_samples": 4,
          "valid_ratio": 0.5,
          "expected_duration_s": 0.1,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 34.425,
          "last_valid_time_s": 34.4625
        },
        "b3-p3": {
          "expected_samples": 9,
          "valid_samples": 4,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 18.875,
          "last_valid_time_s": 18.9125
        },
        "b3-p4": {
          "expected_samples": 9,
          "valid_samples": 4,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 25.5375,
          "last_valid_time_s": 25.575
        },
        "b3-p5": {
          "expected_samples": 9,
          "valid_samples": 4,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 32.2,
          "last_valid_time_s": 32.2375
        }
      }
    },
    "refined": {
      "expected_samples": 142,
      "valid_samples": 66,
      "valid_ratio": 0.4647887323943662,
      "mae_m": 0.08548554927853112,
      "max_abs_error_m": 0.13457897998042423,
      "p95_abs_error_m": 0.11997741657438787,
      "max_positive_bias_m": 0.13457897998042423,
      "passage_count": 8,
      "missed_passage_count": 0,
      "sample_interval_s": 0.00625,
      "expected_duration_s": 0.8875,
      "valid_duration_s": 0.4125,
      "passages": {
        "b1-p3": {
          "expected_samples": 18,
          "valid_samples": 8,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 23.3125,
          "last_valid_time_s": 23.35625
        },
        "b1-p4": {
          "expected_samples": 17,
          "valid_samples": 9,
          "valid_ratio": 0.5294117647058824,
          "expected_duration_s": 0.10625,
          "valid_duration_s": 0.05625,
          "missed": false,
          "first_valid_time_s": 29.975,
          "last_valid_time_s": 30.025
        },
        "b2-p3": {
          "expected_samples": 18,
          "valid_samples": 8,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 21.0875,
          "last_valid_time_s": 21.13125
        },
        "b2-p4": {
          "expected_samples": 18,
          "valid_samples": 8,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 27.75625,
          "last_valid_time_s": 27.8
        },
        "b2-p5": {
          "expected_samples": 17,
          "valid_samples": 9,
          "valid_ratio": 0.5294117647058824,
          "expected_duration_s": 0.10625,
          "valid_duration_s": 0.05625,
          "missed": false,
          "first_valid_time_s": 34.41875,
          "last_valid_time_s": 34.46875
        },
        "b3-p3": {
          "expected_samples": 18,
          "valid_samples": 8,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 18.86875,
          "last_valid_time_s": 18.9125
        },
        "b3-p4": {
          "expected_samples": 18,
          "valid_samples": 8,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 25.53125,
          "last_valid_time_s": 25.575
        },
        "b3-p5": {
          "expected_samples": 18,
          "valid_samples": 8,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 32.2,
          "last_valid_time_s": 32.24375
        }
      }
    },
    "refined_minus_base": {
      "valid_ratio": 0.0,
      "valid_duration_s": 0.0,
      "expected_duration_s": 0.0,
      "mae_m": 8.194585329217297e-05,
      "max_abs_error_m": 0.013568232518960777,
      "p95_abs_error_m": -0.0007063103933724335,
      "max_positive_bias_m": 0.03751666689908628,
      "missed_passage_count": 0
    },
    "passage_comparison": {
      "b1-p3": {
        "base": {
          "expected_samples": 9,
          "valid_samples": 4,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 23.3125,
          "last_valid_time_s": 23.35
        },
        "refined": {
          "expected_samples": 18,
          "valid_samples": 8,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 23.3125,
          "last_valid_time_s": 23.35625
        },
        "validity_class_changed": false,
        "valid_duration_difference_s": 0.0
      },
      "b1-p4": {
        "base": {
          "expected_samples": 9,
          "valid_samples": 5,
          "valid_ratio": 0.5555555555555556,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.0625,
          "missed": false,
          "first_valid_time_s": 29.975,
          "last_valid_time_s": 30.025
        },
        "refined": {
          "expected_samples": 17,
          "valid_samples": 9,
          "valid_ratio": 0.5294117647058824,
          "expected_duration_s": 0.10625,
          "valid_duration_s": 0.05625,
          "missed": false,
          "first_valid_time_s": 29.975,
          "last_valid_time_s": 30.025
        },
        "validity_class_changed": false,
        "valid_duration_difference_s": -0.006249999999999999
      },
      "b2-p3": {
        "base": {
          "expected_samples": 9,
          "valid_samples": 4,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 21.0875,
          "last_valid_time_s": 21.125
        },
        "refined": {
          "expected_samples": 18,
          "valid_samples": 8,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 21.0875,
          "last_valid_time_s": 21.13125
        },
        "validity_class_changed": false,
        "valid_duration_difference_s": 0.0
      },
      "b2-p4": {
        "base": {
          "expected_samples": 9,
          "valid_samples": 4,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 27.7625,
          "last_valid_time_s": 27.8
        },
        "refined": {
          "expected_samples": 18,
          "valid_samples": 8,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 27.75625,
          "last_valid_time_s": 27.8
        },
        "validity_class_changed": false,
        "valid_duration_difference_s": 0.0
      },
      "b2-p5": {
        "base": {
          "expected_samples": 8,
          "valid_samples": 4,
          "valid_ratio": 0.5,
          "expected_duration_s": 0.1,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 34.425,
          "last_valid_time_s": 34.4625
        },
        "refined": {
          "expected_samples": 17,
          "valid_samples": 9,
          "valid_ratio": 0.5294117647058824,
          "expected_duration_s": 0.10625,
          "valid_duration_s": 0.05625,
          "missed": false,
          "first_valid_time_s": 34.41875,
          "last_valid_time_s": 34.46875
        },
        "validity_class_changed": false,
        "valid_duration_difference_s": 0.006249999999999999
      },
      "b3-p3": {
        "base": {
          "expected_samples": 9,
          "valid_samples": 4,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 18.875,
          "last_valid_time_s": 18.9125
        },
        "refined": {
          "expected_samples": 18,
          "valid_samples": 8,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 18.86875,
          "last_valid_time_s": 18.9125
        },
        "validity_class_changed": false,
        "valid_duration_difference_s": 0.0
      },
      "b3-p4": {
        "base": {
          "expected_samples": 9,
          "valid_samples": 4,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 25.5375,
          "last_valid_time_s": 25.575
        },
        "refined": {
          "expected_samples": 18,
          "valid_samples": 8,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 25.53125,
          "last_valid_time_s": 25.575
        },
        "validity_class_changed": false,
        "valid_duration_difference_s": 0.0
      },
      "b3-p5": {
        "base": {
          "expected_samples": 9,
          "valid_samples": 4,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 32.2,
          "last_valid_time_s": 32.2375
        },
        "refined": {
          "expected_samples": 18,
          "valid_samples": 8,
          "valid_ratio": 0.4444444444444444,
          "expected_duration_s": 0.1125,
          "valid_duration_s": 0.05,
          "missed": false,
          "first_valid_time_s": 32.2,
          "last_valid_time_s": 32.24375
        },
        "validity_class_changed": false,
        "valid_duration_difference_s": 0.0
      }
    },
    "verdict": "NUMERICAL_COMPARISON_ONLY",
    "interpretation": "Both independently sampled fixed evaluation grids include misses. Duration uses rectangular sample-count/fps quadrature, with boundary uncertainty of approximately one sample at each transition. Differences combine integration timestep and output-grid effects. No accuracy acceptance tolerance was specified; these differences are evidence, not an automatic convergence pass.",
    "base_run": "results/lidar/raw/close-spatial-v1",
    "refined_run": "results/lidar/raw/close-refined-v1"
  }
}
```
