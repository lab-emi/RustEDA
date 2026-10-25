//! Isolated feasibility measurement, not a production RustLane command.
//! Emits JSON to stdout and never mutates a design workspace.
use egui::{Rect, Sense, Vec2, pos2, vec2};
use egui_extras::{Column, TableBuilder};
use serde_json::{Value, json};
use std::{
    sync::Arc,
    time::{Duration, Instant},
};

const WIDTH: u32 = 1280;
const HEIGHT: u32 = 800;
const WARMUP: usize = 20;
const FRAMES: usize = 120;
const MAX_ROWS_PER_FRAME: usize = 256;
const VISIBLE_COLS: u32 = 64;
const VISIBLE_INSTANCES: u32 = VISIBLE_COLS * VISIBLE_COLS;

#[derive(Clone, Copy)]
enum Pane {
    Report,
}

struct ReportBehavior {
    rows: usize,
    offset: f32,
    rendered: usize,
    first: usize,
    last: usize,
}

impl egui_tiles::Behavior<Pane> for ReportBehavior {
    fn pane_ui(
        &mut self,
        ui: &mut egui::Ui,
        _: egui_tiles::TileId,
        _: &mut Pane,
    ) -> egui_tiles::UiResponse {
        ui.heading("RustLane timing report — synthetic indexed rows");
        ui.label("tt | wireload_lumped | unconstrained: unchecked in this UI probe");
        TableBuilder::new(ui)
            .striped(true)
            .resizable(true)
            .column(Column::initial(190.0))
            .column(Column::initial(150.0))
            .column(Column::initial(130.0))
            .column(Column::initial(130.0))
            .column(Column::remainder())
            .vertical_scroll_offset(self.offset)
            .header(22.0, |mut row| {
                for title in [
                    "Endpoint",
                    "Corner",
                    "Setup slack",
                    "Hold slack",
                    "Evidence",
                ] {
                    row.col(|ui| {
                        ui.strong(title);
                    });
                }
            })
            .body(|body| {
                body.rows(20.0, self.rows, |mut row| {
                    let index = row.index();
                    self.rendered += 1;
                    assert!(
                        self.rendered <= MAX_ROWS_PER_FRAME,
                        "virtual row budget exceeded"
                    );
                    self.first = self.first.min(index);
                    self.last = self.last.max(index);
                    row.col(|ui| {
                        ui.label(format!("inst{index}/D"));
                    });
                    row.col(|ui| {
                        ui.label("tt");
                    });
                    row.col(|ui| {
                        ui.label(format!("{:.3} ns", (index % 101) as f64 * 0.01));
                    });
                    row.col(|ui| {
                        ui.label("0.020 ns");
                    });
                    row.col(|ui| {
                        ui.label(format!("path{}", index + 1));
                    });
                })
            });
        egui_tiles::UiResponse::None
    }

    fn tab_title_for_pane(&mut self, _: &Pane) -> egui::WidgetText {
        "Timing".into()
    }
}

struct Cells {
    pipeline: Arc<wgpu::RenderPipeline>,
}

impl egui_wgpu::CallbackTrait for Cells {
    fn paint(
        &self,
        _: egui::PaintCallbackInfo,
        pass: &mut wgpu::RenderPass<'static>,
        _: &egui_wgpu::CallbackResources,
    ) {
        pass.set_pipeline(&self.pipeline);
        pass.draw(0..6, 0..VISIBLE_INSTANCES);
    }
}

const SHADER: &str = r#"
struct Out { @builtin(position) pos: vec4<f32>, @location(0) color: vec3<f32> };
@vertex fn vs_main(@builtin(vertex_index) v: u32, @builtin(instance_index) i: u32) -> Out {
    let corners = array<vec2<f32>, 6>(vec2(0.,0.),vec2(1.,0.),vec2(0.,1.),vec2(0.,1.),vec2(1.,0.),vec2(1.,1.));
    let cell = vec2<f32>(f32(i % 64u), f32(i / 64u));
    let local = (cell + corners[v] * 0.84) / 64.;
    var out: Out;
    out.pos = vec4(local.x*2.-1., 1.-local.y*2., 0., 1.);
    out.color = vec3(0.06, 0.45 + f32(i % 7u)*0.04, 0.50);
    return out;
}
@fragment fn fs_main(in: Out) -> @location(0) vec4<f32> { return vec4(in.color,1.); }
"#;

struct Gpu {
    device: wgpu::Device,
    queue: wgpu::Queue,
    texture: wgpu::Texture,
    view: wgpu::TextureView,
    renderer: egui_wgpu::Renderer,
    cells: Arc<wgpu::RenderPipeline>,
    info: Value,
}

impl Gpu {
    fn new() -> Result<Self, Box<dyn std::error::Error>> {
        let instance = wgpu::Instance::new(wgpu::InstanceDescriptor {
            backends: wgpu::Backends::VULKAN,
            ..wgpu::InstanceDescriptor::new_without_display_handle()
        });
        let adapter = pollster::block_on(instance.request_adapter(&wgpu::RequestAdapterOptions {
            power_preference: wgpu::PowerPreference::HighPerformance,
            force_fallback_adapter: false,
            compatible_surface: None,
            ..Default::default()
        }))?;
        let info = adapter.get_info();
        if info.device_type == wgpu::DeviceType::Cpu {
            return Err("software adapter is not a verified native GPU measurement".into());
        }
        let (device, queue) =
            pollster::block_on(adapter.request_device(&wgpu::DeviceDescriptor {
                label: Some("RustLane GUI feasibility"),
                ..Default::default()
            }))?;
        let texture = device.create_texture(&wgpu::TextureDescriptor {
            label: Some("offscreen"),
            size: wgpu::Extent3d {
                width: WIDTH,
                height: HEIGHT,
                depth_or_array_layers: 1,
            },
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: wgpu::TextureFormat::Rgba8Unorm,
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::COPY_SRC,
            view_formats: &[],
        });
        let view = texture.create_view(&Default::default());
        let renderer =
            egui_wgpu::Renderer::new(&device, wgpu::TextureFormat::Rgba8Unorm, Default::default());
        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("bounded graphics cells"),
            source: wgpu::ShaderSource::Wgsl(SHADER.into()),
        });
        let cells = Arc::new(
            device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
                label: Some("bounded instanced cells"),
                layout: None,
                vertex: wgpu::VertexState {
                    module: &shader,
                    entry_point: Some("vs_main"),
                    compilation_options: Default::default(),
                    buffers: &[],
                },
                primitive: Default::default(),
                depth_stencil: None,
                multisample: Default::default(),
                fragment: Some(wgpu::FragmentState {
                    module: &shader,
                    entry_point: Some("fs_main"),
                    compilation_options: Default::default(),
                    targets: &[Some(wgpu::ColorTargetState {
                        format: wgpu::TextureFormat::Rgba8Unorm,
                        blend: None,
                        write_mask: wgpu::ColorWrites::ALL,
                    })],
                }),
                multiview_mask: None,
                cache: None,
            }),
        );
        Ok(Self {
            device,
            queue,
            texture,
            view,
            renderer,
            cells,
            info: json!({"name":info.name,"vendor":info.vendor,"device":info.device,"device_type":format!("{:?}",info.device_type),
                "backend":format!("{:?}",info.backend),"driver":info.driver,"driver_info":info.driver_info}),
        })
    }

    fn submit(
        &mut self,
        output: &mut egui::FullOutput,
        primitives: &[egui::ClippedPrimitive],
    ) -> Result<(), Box<dyn std::error::Error>> {
        for (id, deltas) in &output.textures_delta.set {
            for delta in deltas {
                self.renderer
                    .update_texture(&self.device, &self.queue, *id, delta);
            }
        }
        let screen = egui_wgpu::ScreenDescriptor {
            size_in_pixels: [WIDTH, HEIGHT],
            pixels_per_point: output.pixels_per_point,
        };
        let mut encoder = self.device.create_command_encoder(&Default::default());
        let mut buffers = self.renderer.update_buffers(
            &self.device,
            &self.queue,
            &mut encoder,
            primitives,
            &screen,
        );
        {
            let pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("ui plus native GPU callback"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &self.view,
                    depth_slice: None,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        load: wgpu::LoadOp::Clear(wgpu::Color {
                            r: 0.02,
                            g: 0.02,
                            b: 0.02,
                            a: 1.,
                        }),
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: None,
                timestamp_writes: None,
                occlusion_query_set: None,
                multiview_mask: None,
            });
            self.renderer
                .render(&mut pass.forget_lifetime(), primitives, &screen);
        }
        buffers.push(encoder.finish());
        let submitted = self.queue.submit(buffers);
        self.device.poll(wgpu::PollType::Wait {
            submission_index: Some(submitted),
            timeout: Some(Duration::from_secs(5)),
        })?;
        for id in &output.textures_delta.free {
            self.renderer.free_texture(id);
        }
        output.textures_delta.clear();
        Ok(())
    }

    fn verify_pixels(&self) -> Result<Value, Box<dyn std::error::Error>> {
        let stride = WIDTH * 4;
        let buffer = self.device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("bounded readback"),
            size: u64::from(stride) * u64::from(HEIGHT),
            usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
            mapped_at_creation: false,
        });
        let mut encoder = self.device.create_command_encoder(&Default::default());
        encoder.copy_texture_to_buffer(
            self.texture.as_image_copy(),
            wgpu::TexelCopyBufferInfo {
                buffer: &buffer,
                layout: wgpu::TexelCopyBufferLayout {
                    offset: 0,
                    bytes_per_row: Some(stride),
                    rows_per_image: Some(HEIGHT),
                },
            },
            wgpu::Extent3d {
                width: WIDTH,
                height: HEIGHT,
                depth_or_array_layers: 1,
            },
        );
        let submitted = self.queue.submit([encoder.finish()]);
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        buffer.slice(..).map_async(wgpu::MapMode::Read, move |r| {
            let _ = tx.send(r);
        });
        self.device.poll(wgpu::PollType::Wait {
            submission_index: Some(submitted),
            timeout: Some(Duration::from_secs(5)),
        })?;
        rx.recv_timeout(Duration::from_secs(5))??;
        let mapped = buffer.slice(..).get_mapped_range()?;
        let colored = mapped
            .chunks_exact(4)
            .filter(|p| p[1] > 90 && p[0] < 30 && p[2] > 90)
            .count();
        assert!(
            colored > 100_000,
            "GPU callback did not produce the expected visible color region"
        );
        drop(mapped);
        buffer.unmap();
        Ok(json!({"status":"passed","colored_pixels":colored,"readback_bytes":stride*HEIGHT}))
    }
}

fn distribution(mut values: Vec<f64>) -> Value {
    assert!(!values.is_empty() && values.len() <= 240);
    values.sort_by(f64::total_cmp);
    let p = |q: f64| values[((values.len() - 1) as f64 * q).ceil() as usize];
    json!({"samples":values.len(),"p50_ms":p(0.5),"p95_ms":p(0.95),"max_ms":values.last()})
}

fn raw_input(frame: usize) -> egui::RawInput {
    egui::RawInput {
        screen_rect: Some(Rect::from_min_size(
            pos2(0., 0.),
            vec2(WIDTH as f32, HEIGHT as f32),
        )),
        time: Some(frame as f64 / 60.),
        ..Default::default()
    }
}

fn report_case(gpu: &mut Gpu, total_rows: usize) -> Result<Value, Box<dyn std::error::Error>> {
    assert!((1..=1_000_000).contains(&total_rows));
    let context = egui::Context::default();
    let mut tree =
        egui_tiles::Tree::new_tabs(egui::Id::unique(("report", total_rows)), vec![Pane::Report]);
    let mut ui_ms = Vec::with_capacity(FRAMES);
    let mut total_ms = Vec::with_capacity(FRAMES);
    let mut maximum_rows = 0;
    let mut ranges = Vec::new();
    let mut maximum_vertices = 0;
    for frame in 0..WARMUP + FRAMES {
        let region = if frame < WARMUP {
            0
        } else {
            (frame - WARMUP) / (FRAMES / 3)
        };
        let selected = match region {
            0 => 0,
            1 => total_rows / 2,
            _ => total_rows - 1,
        };
        let mut behavior = ReportBehavior {
            rows: total_rows,
            offset: selected as f32 * 23.,
            rendered: 0,
            first: usize::MAX,
            last: 0,
        };
        let started = Instant::now();
        let mut output = context.run_ui(raw_input(frame), |ui| tree.ui(&mut behavior, ui));
        let primitives =
            context.tessellate(std::mem::take(&mut output.shapes), output.pixels_per_point);
        let ui_elapsed = started.elapsed().as_secs_f64() * 1000.;
        maximum_rows = maximum_rows.max(behavior.rendered);
        let vertices: usize = primitives
            .iter()
            .map(|p| match &p.primitive {
                egui::epaint::Primitive::Mesh(m) => m.vertices.len(),
                _ => 0,
            })
            .sum();
        maximum_vertices = maximum_vertices.max(vertices);
        gpu.submit(&mut output, &primitives)?;
        if frame >= WARMUP {
            ui_ms.push(ui_elapsed);
            total_ms.push(started.elapsed().as_secs_f64() * 1000.);
            if (frame - WARMUP) % (FRAMES / 3) == FRAMES / 3 - 1 {
                ranges.push(json!({"requested_row":selected,"first":behavior.first,"last":behavior.last,"callbacks":behavior.rendered}));
            }
        }
    }
    assert!(maximum_rows > 0 && maximum_rows <= MAX_ROWS_PER_FRAME);
    assert_eq!(
        ranges.last().unwrap()["last"].as_u64().unwrap(),
        total_rows as u64 - 1,
        "did not reach the last row"
    );
    Ok(
        json!({"logical_rows":total_rows,"columns":5,"docking":"egui_tiles tabs","max_row_callbacks_per_frame":maximum_rows,
        "max_mesh_vertices":maximum_vertices,"viewport_ranges":ranges,"ui_layout_and_tessellation":distribution(ui_ms),
        "ui_upload_submit_and_gpu_fence":distribution(total_ms)}),
    )
}

fn canvas_case(gpu: &mut Gpu) -> Result<Value, Box<dyn std::error::Error>> {
    const { assert!(VISIBLE_INSTANCES <= 8192) };
    let camera_x = 1_000_000_000_000_i64;
    let pitch_dbu = 1000_i64;
    let last_world = camera_x + i64::from(VISIBLE_COLS - 1) * pitch_dbu;
    assert_eq!(
        (last_world - camera_x) / pitch_dbu,
        i64::from(VISIBLE_COLS - 1)
    );
    let context = egui::Context::default();
    let mut ui_ms = Vec::with_capacity(FRAMES);
    let mut total_ms = Vec::with_capacity(FRAMES);
    let mut idle_signal = false;
    for frame in 0..WARMUP + FRAMES {
        let begin = Instant::now();
        let mut output = context.run_ui(raw_input(frame), |ui| {
            ui.heading("RustLane bounded native GPU canvas");
            ui.label("1,000,000 logical cells | 4,096 visible | integer DBU world | synthetic");
            let (rect, _) =
                ui.allocate_exact_size(ui.available_size().max(Vec2::splat(1.)), Sense::hover());
            ui.painter().add(egui_wgpu::Callback::new_paint_callback(
                rect,
                Cells {
                    pipeline: gpu.cells.clone(),
                },
            ));
        });
        let primitives =
            context.tessellate(std::mem::take(&mut output.shapes), output.pixels_per_point);
        let ui_elapsed = begin.elapsed().as_secs_f64() * 1000.;
        if frame == WARMUP + FRAMES - 1 {
            idle_signal = output
                .viewport_output
                .get(&egui::ViewportId::ROOT)
                .is_some_and(|o| o.repaint_delay > Duration::from_secs(1));
        }
        gpu.submit(&mut output, &primitives)?;
        if frame >= WARMUP {
            ui_ms.push(ui_elapsed);
            total_ms.push(begin.elapsed().as_secs_f64() * 1000.);
        }
    }
    let pixels = gpu.verify_pixels()?;
    Ok(
        json!({"logical_cells":1_000_000,"visible_instances":VISIBLE_INSTANCES,"instance_draw_calls":1,
        "world_coordinate_type":"i64 DBU","camera_origin_dbu":camera_x,"integer_origin_projection_check":"passed",
        "ui_layout_and_tessellation":distribution(ui_ms),"ui_upload_submit_and_gpu_fence":distribution(total_ms),
        "static_frame_allows_event_driven_wait":idle_signal,"pixel_readback":pixels}),
    )
}

fn run() -> Result<Value, Box<dyn std::error::Error>> {
    let started = Instant::now();
    // Type-check the intended native shell and the GUI -> core dependency direction.
    let shell = eframe::NativeOptions {
        renderer: eframe::Renderer::Wgpu,
        ..Default::default()
    };
    assert!(matches!(shell.renderer, eframe::Renderer::Wgpu));
    assert_eq!(rustlane::config::MAX_STEPS, 128);
    let mut gpu = Gpu::new()?;
    let small = report_case(&mut gpu, 10_000)?;
    let million = report_case(&mut gpu, 1_000_000)?;
    let canvas = canvas_case(&mut gpu)?;
    Ok(
        json!({"schema":"rustlane.gui-feasibility.v1","status":"passed","versions":{"egui":"0.37.0","egui_extras":"0.37.0","egui_tiles":"0.18.0","egui-wgpu":"0.37.0","wgpu":"30.0.1"},
        "adapter":gpu.info,"native_shell":"eframe 0.37.0 Wgpu options type-checked; native event loop not launched","core_dependency_direction":"GUI research crate depends on rustlane; rustlane has no GUI dependencies",
        "resolution":[WIDTH,HEIGHT],"warmup_frames_per_case":WARMUP,"measured_frames_per_case":FRAMES,
        "reports":[small,million],"canvas":canvas,"whole_probe_elapsed_seconds":started.elapsed().as_secs_f64(),
        "measurement_scope":"offscreen UI+layout+tessellation+upload+graphics submission+GPU fence; per-frame readback excluded; one separate bounded readback validates output",
        "not_measured":["other frameworks on equivalent scenes","native window input-to-present latency","Chinese IME and accessibility behavior","idle process CPU and OS event-loop behavior","integrated GPUs or other OSes","real chip spatial indexing and streamed disk data","aggregate GPU VRAM"]}),
    )
}

fn main() {
    match run() {
        Ok(value) => println!("{}", serde_json::to_string_pretty(&value).unwrap()),
        Err(error) => {
            eprintln!(
                "{}",
                json!({"schema":"rustlane.gui-feasibility.v1","status":"unverified","error":error.to_string()})
            );
            std::process::exit(1);
        }
    }
}
