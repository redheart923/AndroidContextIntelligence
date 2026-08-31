package com.example

class KDemo {
    external fun read(data: ByteArray): String
    external fun overloaded(value: Int): Int
    external fun overloaded(value: String): Int

    class Inner {
        external fun ping(values: Array<String>): Unit
    }
}
