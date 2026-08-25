pub mod bridge {
    use crate::model::Device;

    pub trait Runner {
        fn run(&self) -> i32;
    }

    pub struct Engine;

    impl Runner for Engine {
        fn run(&self) -> i32 { 1 }
    }

    impl Engine {
        pub fn new() -> Self { Self }
    }

    #[no_mangle]
    pub extern "C" fn native_start(value: i32) -> i32 { value }

    #[export_name = "demo_alias"]
    pub extern "C" fn alias(value: i32) -> i32 { value }

    unsafe extern "C" {
        #[link_name = "platform_open"]
        fn platform_open_bridge(value: i32) -> i32;
    }

    #[cfg(future_android)]
    pub fn conditional() {}

    generate_bindings!();
}
