"""Native camera HUD and static installation schematic drawing."""


def draw(self):
    bpy = self._native['bpy']
    if bpy.context.window != self.window or bpy.context.workspace != self.workspace:return
    entry=next((e for e in self.entries if e['area']==bpy.context.area),None)
    if not entry:return
    from . import performance, playback
    performance.count('native.draw')
    import gpu,blf
    from gpu_extras.batch import batch_for_shader
    region=bpy.context.region
    shader=gpu.shader.from_builtin('UNIFORM_COLOR')
    def rect(x,y,w,h):
        if w<=0 or h<=0:return
        shader.bind()
        shader.uniform_float('color',(.015,.02,.025,1))
        batch_for_shader(shader,'TRI_FAN',{'pos':[(x,y),(x+w,y),(x+w,y+h),(x,y+h)]}).draw(shader)
    proxy=entry['proxy']
    if not proxy:rect(0,0,region.width,region.height)
    label=(f"C{entry['slot']} · " + ('叶根为主', '中段为主', '叶尖为主')[entry['slot']-1]) if proxy else f"C{entry['slot']} · 未安装或未参与"
    scale=bpy.context.preferences.system.ui_scale
    from .custom_camera_preview import available_rectangle
    _, bottom, _, height = available_rectangle(bpy.context.area, region)
    title_top=bottom+height
    rect(0,title_top-28*scale,region.width,28*scale)
    font=0
    blf.size(font,15*scale)
    blf.color(font,.88,.94,1,1)
    blf.position(font,10*scale,title_top-21*scale,0)
    blf.draw(font,label)
    if entry is self.entries[0]:
        label_width,_=blf.dimensions(font,label)
        time_s=self.scene.get('wfrl_clearance_time_s')
        clock=f'共同 {time_s:.2f} s' if time_s is not None else f'共同帧 {self.scene.frame_current}'
        clock += ' · 播放' if playback.is_playing(self.scene) else ' · 暂停'
        blf.size(font,11*scale)
        blf.color(font,.65,.73,.81,1)
        width,_=blf.dimensions(font,clock)
        available=max(1,region.width-label_width-30*scale)
        if width>available:
            blf.size(font,11*scale*available/width)
            width,_=blf.dimensions(font,clock)
        blf.position(font,region.width-width-10*scale,title_top-20*scale,0)
        blf.draw(font,clock)


def draw_diagram(self):
    bpy = self._native['bpy']
    schematic = self._native['schematic']
    if bpy.context.window != self.window or bpy.context.area != self.diagram_area:return
    import gpu, blf
    from gpu_extras.batch import batch_for_shader
    region=bpy.context.region
    scale=min(bpy.context.preferences.system.ui_scale,region.width/400,region.height/230)
    shader=gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.bind()
    shader.uniform_float('color',(.018,.025,.034,1.))
    batch_for_shader(shader,'TRI_FAN',{'pos':[(0,0),(region.width,0),(region.width,region.height),(0,region.height)]}).draw(shader)
    def text(value,x,y,size=13,color=(.78,.85,.92,1.)):
        blf.size(0,size*scale)
        blf.color(0,*color)
        blf.position(0,x,y,0)
        blf.draw(0,value)
    text('安装位置示意 · 静态',16*scale,region.height-28*scale,18)
    if self.diagram_stale or self.diagram is None:
        text('旧图已失效 · '+(self.diagram_error or '正在更新当前安装配置'),16*scale,region.height-57*scale,12,(1.,.66,.3,1.))
        return
    text('当前实际几何 · 非第四路实时画面',16*scale,region.height-49*scale,11)
    xgap=12*scale
    available=max(12.,region.width-3*xgap)
    panels=[(xgap,45*scale,available*.45,max(10.,region.height-122*scale)),
            (2*xgap+available*.45,45*scale,available*.55,max(10.,region.height-122*scale))]
    batch_key=(self.diagram_key,region.width,region.height,scale)
    if self.diagram_batches is None or self.diagram_batches[0] != batch_key:
        vertices,colors,transforms=[],[],[]
        for bounds,rect in zip((self.diagram['overview'],self.diagram['detail']),panels):
            factor,tx,ty=schematic.fit_transform(bounds,rect)
            transforms.append((factor,tx,ty))
            for _,triangle,color in self.diagram['triangles']:
                points=schematic.clipped_polygon([(p[0]*factor+tx,p[1]*factor+ty) for p in triangle],rect)
                for i in range(1,len(points)-1):
                    vertices.extend((points[0],points[i],points[i+1]))
                    colors.extend((color,)*3)
        colored=gpu.shader.from_builtin('FLAT_COLOR')
        batch=batch_for_shader(colored,'TRIS',{'pos':vertices,'color':colors}) if vertices else None
        self.diagram_batches=(batch_key,colored,batch,transforms)
    _,colored,batch,transforms=self.diagram_batches
    if batch:
        colored.bind()
        batch.draw(colored)
    text('机舱与安装位置',panels[0][0],region.height-70*scale,11)
    text('盒体 / 支架局部',panels[1][0],region.height-70*scale,11)
    factor,tx,ty=transforms[1]
    for label,point in self.diagram['labels']:
        x,y=point[0]*factor+tx,point[1]*factor+ty
        if panels[1][0] <= x <= region.width-24*scale and 45*scale <= y <= region.height-80*scale:
            text(label,x+4*scale,y,10,(1.,.86,.5,1.))
    text('机舱局部坐标 · 安装调整后自动更新',16*scale,23*scale,10)
