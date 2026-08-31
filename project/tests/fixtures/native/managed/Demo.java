package com.example;

public class Demo {
    public native int sum(int left, int right);
    public native String[] names(byte[] input);
    public native int overloaded(int value);
    public native int overloaded(String value);

    public static class Inner {
        public native void ping(String value);
    }
}
