package android.content;

public class Context {
    public void enforceCallingPermission(String permission, String message) {}
    public void enforceCallingOrSelfPermission(String permission, String message) {}
    public int checkCallingPermission(String permission) { return 0; }
    public int checkCallingOrSelfPermission(String permission) { return 0; }
}
