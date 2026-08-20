package fixture.security;

import android.content.Context;
import android.os.Binder;

class SensitiveStore {
    static void writeSecureSetting(String value) {}
}

public class SecurityService extends Binder {
    private final Context context = new Context();

    public void guarded(String value) {
        context.enforceCallingPermission("fixture.PERMISSION", "required");
        SensitiveStore.writeSecureSetting(value);
    }

    public void unguarded(String value) {
        SensitiveStore.writeSecureSetting(value);
    }

    public void pairedIdentity(String value) {
        long token = Binder.clearCallingIdentity();
        try {
            SensitiveStore.writeSecureSetting(value);
        } finally {
            Binder.restoreCallingIdentity(token);
        }
    }

    public void brokenIdentity(String value, boolean restore) {
        long token = Binder.clearCallingIdentity();
        SensitiveStore.writeSecureSetting(value);
        if (restore) {
            Binder.restoreCallingIdentity(token);
        }
    }

    public void nestedIdentity(String value) {
        long outer = Binder.clearCallingIdentity();
        long inner = Binder.clearCallingIdentity();
        try {
            SensitiveStore.writeSecureSetting(value);
        } finally {
            Binder.restoreCallingIdentity(inner);
        }
    }

    public void throughHelper(String value) {
        forward(value);
    }

    private void forward(String forwarded) {
        SensitiveStore.writeSecureSetting(forwarded);
    }
}
