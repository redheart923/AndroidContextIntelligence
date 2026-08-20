package fixture.calls;

interface Worker {
    String work(String value);
}

class BaseWorker implements Worker {
    BaseWorker() {}
    BaseWorker(int seed) {}

    @Override
    public String work(String value) {
        return value.trim();
    }

    protected String inherited(String value) {
        return value;
    }
}

final class FinalWorker extends BaseWorker {
    FinalWorker() {
        super(1);
    }

    @Override
    public String work(String value) {
        return super.inherited(value);
    }
}

class Calls {
    static String overloaded(String value) { return value; }
    static String overloaded(int value) { return Integer.toString(value); }

    String exercise(Worker worker) {
        Worker anonymous = new Worker() {
            @Override public String work(String value) { return value.toUpperCase(); }
        };
        Runnable lambda = () -> overloaded(7);
        lambda.run();
        return worker.work(anonymous.work(overloaded(" value ")));
    }
}
