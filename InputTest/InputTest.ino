/*
  DJ Bop-It — input tester
  Board: Arduino Nano. No libraries needed.

  Upload, open Serial Monitor at 115200, and use every control.
  Each change is printed as it happens, and a checklist tracks what has
  been confirmed working. The Nano's built-in LED (D13) blinks on every event.

  Serial Monitor commands (type and press Enter):
    s  show the checklist
    r  reset the checklist
    v  print all raw values once
*/

const uint8_t PIN_ENC_CLK  = 2;
const uint8_t PIN_ENC_DT   = 3;
const uint8_t PIN_BTN_A    = 4;
const uint8_t PIN_BTN_B    = 5;
const uint8_t PIN_SW_1     = 6;
const uint8_t PIN_SW_2     = 7;
const uint8_t PIN_SLIDER_A = A0;
const uint8_t PIN_SLIDER_B = A1;
const uint8_t PIN_LED      = LED_BUILTIN;

const unsigned long DEBOUNCE_MS = 25;
const int SLIDER_PRINT_STEP = 25;   // only print a slider when it moves this much
const int SLIDER_LOW  = 50;         // slider counts as "at bottom" below this
const int SLIDER_HIGH = 973;        // and "at top" above this
const int SPIN_STEPS  = 8;          // encoder steps counted as a real turn

// ------------------------------ Checklist -------------------------------

enum Check : uint8_t {
  CHK_BTN_A, CHK_BTN_B, CHK_SW_1, CHK_SW_2, CHK_ENC_CW, CHK_ENC_CCW,
  CHK_A0_LOW, CHK_A0_HIGH, CHK_A1_LOW, CHK_A1_HIGH, NUM_CHECKS
};

const char *const CHECK_NAMES[NUM_CHECKS] = {
  "Button D4 pressed", "Button D5 pressed", "Switch side 1 (D6)", "Switch side 2 (D7)",
  "Encoder clockwise", "Encoder counter-clockwise",
  "Slider A0 at bottom", "Slider A0 at top", "Slider A1 at bottom", "Slider A1 at top"
};

bool passed[NUM_CHECKS];

// Defined up here, before any function, because the Arduino IDE inserts
// function prototypes above the first function and they need this type.
struct Slider {
  uint8_t pin;
  const char *name;
  Check lowCheck, highCheck;
  int lastPrinted;
  float smoothed;
  bool connected;
  unsigned long lastPrintMs, lastConnCheckMs;
};

Slider sliders[2] = {
  {PIN_SLIDER_A, "Slider A0: ", CHK_A0_LOW, CHK_A0_HIGH},
  {PIN_SLIDER_B, "Slider A1: ", CHK_A1_LOW, CHK_A1_HIGH},
};
bool allPassedAnnounced = false;

void printChecklist() {
  Serial.println(F("\n---- Checklist ----"));
  for (uint8_t i = 0; i < NUM_CHECKS; i++) {
    Serial.print(passed[i] ? F("[x] ") : F("[ ] "));
    Serial.println(CHECK_NAMES[i]);
  }
  Serial.println(F("-------------------"));
}

void resetChecklist() {
  for (uint8_t i = 0; i < NUM_CHECKS; i++) passed[i] = false;
  allPassedAnnounced = false;
  Serial.println(F("Checklist reset."));
}

unsigned long ledOffAt = 0;

void blink() {
  digitalWrite(PIN_LED, HIGH);
  ledOffAt = millis() + 80;
}

void mark(Check c) {
  if (passed[c]) return;
  passed[c] = true;
  Serial.print(F("  PASS: "));
  Serial.println(CHECK_NAMES[c]);

  for (uint8_t i = 0; i < NUM_CHECKS; i++) if (!passed[i]) return;
  if (!allPassedAnnounced) {
    allPassedAnnounced = true;
    Serial.println(F("\n*** ALL INPUTS WORKING ***\n"));
  }
}

// ------------------------------- Inputs ---------------------------------

struct Debouncer {
  uint8_t stable, lastRaw;
  unsigned long changedAt;

  void reset(uint8_t raw) { stable = lastRaw = raw; changedAt = millis(); }

  bool update(uint8_t raw) {
    unsigned long now = millis();
    if (raw != lastRaw) {
      lastRaw = raw;
      changedAt = now;
    } else if (raw != stable && now - changedAt >= DEBOUNCE_MS) {
      stable = raw;
      return true;
    }
    return false;
  }
};

Debouncer buttonA, buttonB, switchPos;

volatile int encoderSteps = 0;
volatile uint8_t encoderState = 0;
volatile unsigned int encoderEdges = 0;   // raw edge count, to spot a dead pin

void encoderISR() {
  static const int8_t table[16] = {0, -1, 1, 0, 1, 0, 0, -1, -1, 0, 0, 1, 0, 1, -1, 0};
  encoderState = ((encoderState << 2) | (digitalRead(PIN_ENC_CLK) << 1) | digitalRead(PIN_ENC_DT)) & 0x0F;
  encoderSteps += table[encoderState];
  encoderEdges++;
}

bool pressed(uint8_t pin) { return digitalRead(pin) == LOW; }

uint8_t readSwitch() {
  bool s1 = pressed(PIN_SW_1), s2 = pressed(PIN_SW_2);
  if (s1 && s2) return 3;   // both LOW = wiring problem
  if (s1) return 1;
  if (s2) return 2;
  return 0;                 // neither = middle, or a loose wire
}

int readSlider(uint8_t pin) {
  // The ADC is shared between pins; the first reading after switching from
  // A0 to A1 still "remembers" A0, so throw it away.
  analogRead(pin);
  delayMicroseconds(100);
  long sum = 0;
  for (uint8_t i = 0; i < 8; i++) sum += analogRead(pin);
  return sum / 8;
}

// A wiper that isn't connected leaves the pin floating, which reads as random
// values. Briefly turn on the internal pull-up: a connected slider barely
// changes, a floating pin jumps up to ~1023.
bool sliderConnected(uint8_t pin) {
  int normal = readSlider(pin);
  pinMode(pin, INPUT_PULLUP);
  delay(2);
  int pulled = readSlider(pin);
  pinMode(pin, INPUT);
  delay(2);
  return pulled - normal < 150;
}

void printSwitch(uint8_t s) {
  Serial.print(F("Switch: "));
  switch (s) {
    case 1: Serial.println(F("side 1 (D6 LOW)")); mark(CHK_SW_1); break;
    case 2: Serial.println(F("side 2 (D7 LOW)")); mark(CHK_SW_2); break;
    case 3: Serial.println(F("BOTH D6 and D7 LOW - check switch wiring!")); break;
    default: Serial.println(F("neither side (middle, or check common -> GND)")); break;
  }
}

void printAll() {
  noInterrupts();
  int steps = encoderSteps;
  unsigned int edges = encoderEdges;
  interrupts();
  Serial.print(F("D2(CLK)=")); Serial.print(digitalRead(PIN_ENC_CLK));
  Serial.print(F(" D3(DT)=")); Serial.print(digitalRead(PIN_ENC_DT));
  Serial.print(F(" enc steps=")); Serial.print(steps);
  Serial.print(F(" edges=")); Serial.print(edges);
  Serial.print(F(" | D4=")); Serial.print(digitalRead(PIN_BTN_A));
  Serial.print(F(" D5=")); Serial.print(digitalRead(PIN_BTN_B));
  Serial.print(F(" D6=")); Serial.print(digitalRead(PIN_SW_1));
  Serial.print(F(" D7=")); Serial.print(digitalRead(PIN_SW_2));
  Serial.print(F(" | A0=")); Serial.print(readSlider(PIN_SLIDER_A));
  Serial.print(F(" A1=")); Serial.println(readSlider(PIN_SLIDER_B));
}

// --------------------------- Per-input checks ---------------------------

void checkButtons() {
  if (buttonA.update(pressed(PIN_BTN_A))) {
    blink();
    Serial.println(buttonA.stable ? F("Button D4: PRESSED") : F("Button D4: released"));
    if (buttonA.stable) mark(CHK_BTN_A);
  }
  if (buttonB.update(pressed(PIN_BTN_B))) {
    blink();
    Serial.println(buttonB.stable ? F("Button D5: PRESSED") : F("Button D5: released"));
    if (buttonB.stable) mark(CHK_BTN_B);
  }
}

void checkSwitch() {
  if (switchPos.update(readSwitch())) {
    blink();
    printSwitch(switchPos.stable);
  }
}

void checkEncoder() {
  static int lastPrinted = 0;
  static int turnStart = 0;

  noInterrupts();
  int steps = encoderSteps;
  interrupts();

  if (steps != lastPrinted) {
    blink();
    Serial.print(F("Encoder: "));
    Serial.print(steps > lastPrinted ? F("clockwise   ") : F("counter-cw  "));
    Serial.print(F("position "));
    Serial.println(steps);
    lastPrinted = steps;
  }
  if (steps - turnStart >= SPIN_STEPS)  { mark(CHK_ENC_CW);  turnStart = steps; }
  if (steps - turnStart <= -SPIN_STEPS) { mark(CHK_ENC_CCW); turnStart = steps; }
}

void checkConnection(Slider &sl) {
  bool ok = sliderConnected(sl.pin);
  if (ok != sl.connected) {
    sl.connected = ok;
    Serial.print(sl.name);
    Serial.println(ok ? F("connected") :
      F("NOT CONNECTED - pin is floating. Check the middle (wiper) pin goes to the Arduino, and the end pins go to 5V and GND."));
    if (ok) sl.smoothed = sl.lastPrinted = readSlider(sl.pin);
  }
  sl.lastConnCheckMs = millis();
}

void checkSlider(Slider &sl) {
  if (millis() - sl.lastConnCheckMs >= 1000) checkConnection(sl);
  if (!sl.connected) return;

  // Smooth out ADC jitter so a slider sitting still doesn't print.
  sl.smoothed += (readSlider(sl.pin) - sl.smoothed) * 0.3;
  int v = (int)(sl.smoothed + 0.5);

  if (abs(v - sl.lastPrinted) >= SLIDER_PRINT_STEP && millis() - sl.lastPrintMs >= 100) {
    blink();
    Serial.print(sl.name);
    Serial.print(v);
    Serial.print(F("  "));
    // little bar graph, 0..1023 -> 0..20 chars
    for (int i = 0; i < 20; i++) Serial.print(i < v / 51 ? '#' : '.');
    Serial.println();
    sl.lastPrinted = v;
    sl.lastPrintMs = millis();
  }
  if (v <= SLIDER_LOW) mark(sl.lowCheck);
  if (v >= SLIDER_HIGH) mark(sl.highCheck);
}

// ------------------------------- Main -----------------------------------

void setup() {
  Serial.begin(115200);

  pinMode(PIN_ENC_CLK, INPUT_PULLUP);
  pinMode(PIN_ENC_DT, INPUT_PULLUP);
  pinMode(PIN_BTN_A, INPUT_PULLUP);
  pinMode(PIN_BTN_B, INPUT_PULLUP);
  pinMode(PIN_SW_1, INPUT_PULLUP);
  pinMode(PIN_SW_2, INPUT_PULLUP);
  pinMode(PIN_LED, OUTPUT);

  attachInterrupt(digitalPinToInterrupt(PIN_ENC_CLK), encoderISR, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_ENC_DT), encoderISR, CHANGE);

  buttonA.reset(pressed(PIN_BTN_A));
  buttonB.reset(pressed(PIN_BTN_B));
  switchPos.reset(readSwitch());
  for (Slider &sl : sliders) {
    sl.connected = true;   // so checkConnection only reports a problem
    sl.smoothed = sl.lastPrinted = readSlider(sl.pin);
    checkConnection(sl);
  }

  Serial.println(F("\n=== DJ Bop-It input tester ==="));
  Serial.println(F("Use every control. Type s = checklist, r = reset, v = raw values.\n"));

  // Startup warnings for things that are usually wiring mistakes.
  printAll();
  if (buttonA.stable) Serial.println(F("WARNING: D4 reads pressed at startup - button stuck or wired to wrong pin?"));
  if (buttonB.stable) Serial.println(F("WARNING: D5 reads pressed at startup - button stuck or wired to wrong pin?"));
  printSwitch(switchPos.stable);
  printChecklist();
}

void loop() {
  checkButtons();
  checkSwitch();
  checkEncoder();
  for (Slider &sl : sliders) checkSlider(sl);

  if (Serial.available()) {
    char c = Serial.read();
    if (c == 's') printChecklist();
    else if (c == 'r') resetChecklist();
    else if (c == 'v') printAll();
  }

  if (ledOffAt && millis() >= ledOffAt) {
    digitalWrite(PIN_LED, LOW);
    ledOffAt = 0;
  }
}
