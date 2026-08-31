#include <jni.h>

static jint nativeSum(JNIEnv*, jobject, jint left, jint right) {
    return left + right;
}

static void nativePing(JNIEnv*, jobject, jstring) {}

static const JNINativeMethod gDemoMethods[] = {
    {"sum", "(II)I", reinterpret_cast<void*>(nativeSum)},
};

static const JNINativeMethod gInnerMethods[] = {
    {"ping", "(Ljava/lang/String;)V", (void*)nativePing},
};

static const JNINativeMethod gDirectMethods[] = {
    {"sum", "(II)I", (void*)nativeSum},
};

int registerDemo(JNIEnv* env) {
    jniRegisterNativeMethods(
        env, "com/example/Demo", gDemoMethods, NELEM(gDemoMethods));
    AndroidRuntime::registerNativeMethods(
        env, "com/example/Demo$Inner", gInnerMethods, NELEM(gInnerMethods));
    jclass clazz = env->FindClass("com/example/Demo");
    return env->RegisterNatives(clazz, gDirectMethods, NELEM(gDirectMethods));
}
