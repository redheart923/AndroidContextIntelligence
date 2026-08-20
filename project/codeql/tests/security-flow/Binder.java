package android.os;

public class Binder {
    public static int getCallingUid() { return 1000; }
    public static int getCallingPid() { return 42; }
    public static long clearCallingIdentity() { return 1L; }
    public static void restoreCallingIdentity(long token) {}
}
