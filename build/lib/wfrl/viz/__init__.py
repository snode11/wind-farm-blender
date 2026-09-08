"""可视化：流场提取、3D 机组模型、RViz 风格交互界面。

  field     从 FLORIS 求解器取水平截面速度场 / 机组几何
  animate   PyVista 机组建模与逐帧更新（被 rviz_app 复用）
  rviz_app  PyVistaQt 交互界面：拖拽重定位机组、实时遥测、双后端切换
"""
