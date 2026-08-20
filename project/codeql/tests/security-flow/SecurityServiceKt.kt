package fixture.security

import android.os.Binder

class SecurityServiceKt : Binder() {
    fun unguarded(value: String) {
        SensitiveStore.writeSecureSetting(value)
    }
}
