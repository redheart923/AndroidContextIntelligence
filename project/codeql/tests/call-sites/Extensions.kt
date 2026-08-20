package fixture.calls

fun String.decorate(suffix: String): String = this + suffix

class KotlinCalls {
    fun exercise(value: String): String = value.decorate("!").trim()
}
