/*
  DJ Bop-It  —  Team 5 prototype
  Board: Arduino Nano (ATmega328P)

  The game calls out a command ("Volume up!"), the player has a limited time
  to do it, a ding plays on success and the next command comes a little
  faster. A wrong input or running out of time ends the round and the score
  is read out. Players take turns and try to beat the high score.

  Wiring (matches the schematic):
    D2  encoder CLK        D6  switch side 1   (switch common -> GND)
    D3  encoder DT         D7  switch side 2
    D4  button "Drop the beat"   (other leg -> GND)
    D5  button "Air horn"        (other leg -> GND)
    D10 Arduino RX  <- DFPlayer TX
    D11 Arduino TX  -> 1k resistor -> DFPlayer RX
    A0  slider "Volume"  (ends to 5V/GND, wiper to A0)
    A1  slider "Tempo"
    Optional: DFPlayer BUSY -> D8 (set BUSY_PIN below). Without it the
    sketch uses fixed wait times instead.

  Library: "DFRobotDFPlayerMini" by DFRobot (Arduino Library Manager).

  SD card (FAT32), made by make_sd_card.py:
    /01/001..009   command voice lines, in the same order as CommandId
    /02/001..008   sound effects / announcer lines (see Sfx)
    /03/001..151   numbers 0..150 for the score readout (file = score + 1)

  First power-up: set INPUT_TEST_MODE to 1. The voice asks for each control
  in turn ("Next one: Volume up!"), dings when it's right and tells you what
  it got when it's wrong. Progress is also printed at 115200 baud. Flip the
  *_REVERSE settings if anything is backwards, then set it back to 0.
*/

#include <SoftwareSerial.h>
#include <DFRobotDFPlayerMini.h>

// ------------------------------- Settings -------------------------------

#define INPUT_TEST_MODE 0      // 1 = guided voice test of every input
#define LAPTOP_AUDIO 1         // 1 = play sounds on the laptop via laptop_audio.py
                               //     instead of the DFPlayer (close Serial Monitor first)

const int8_t  BUSY_PIN       = -1;   // set to 8 if DFPlayer BUSY is wired to D8
const uint8_t MP3_VOLUME     = 25;   // 0..30

const bool ENCODER_REVERSE   = false;  // swap "spin right" / "spin left"
const bool SLIDER_A_REVERSE  = false;  // swap "volume up" / "volume down"
const bool SLIDER_B_REVERSE  = false;  // swap "tempo up" / "tempo down"

const int SPIN_STEPS         = 8;    // encoder steps (4 per click on most KY-040s)
const int SLIDE_THRESHOLD_A  = 300;  // how far slider A0 must move (0..1023)
const int SLIDE_THRESHOLD_B  = 450;  // how far slider A1 must move (bigger = longer push)
const unsigned long DEBOUNCE_MS = 25;

// Time allowed per command: starts at START, drops by STEP per point, never below MIN.
const unsigned long TIME_LIMIT_START_MS = 5000;
const unsigned long TIME_LIMIT_MIN_MS   = 1500;
const unsigned long TIME_LIMIT_STEP_MS  = 150;

// Wait times used when BUSY_PIN is not wired (a bit longer than each clip).
const unsigned long WAIT_SUCCESS_MS = 600;
const unsigned long WAIT_VOICE_MS   = 1300;
const unsigned long WAIT_NUMBER_MS  = 1300;
const unsigned long WAIT_INTRO_MS   = 2000;
const unsigned long WAIT_SHORT_MS   = 800;
const unsigned long WAIT_LONG_MS    = 2800;

// --------------------------------- Pins ---------------------------------

const uint8_t PIN_ENC_CLK = 2;
const uint8_t PIN_ENC_DT  = 3;
const uint8_t PIN_BTN_A   = 4;
const uint8_t PIN_BTN_B   = 5;
const uint8_t PIN_SW_1    = 6;
const uint8_t PIN_SW_2    = 7;
const uint8_t PIN_MP3_RX  = 10;
const uint8_t PIN_MP3_TX  = 11;
const uint8_t PIN_SLIDER_A = A0;
const uint8_t PIN_SLIDER_B = A1;

// ----------------------------- Audio files ------------------------------

const uint8_t FOLDER_COMMANDS = 1;
const uint8_t FOLDER_SFX      = 2;
const uint8_t FOLDER_NUMBERS  = 3;
const int     MAX_SCORE_CLIP  = 150;

enum Sfx : uint8_t {
  SFX_PRESS_TO_PLAY = 1,
  SFX_SUCCESS       = 2,
  SFX_FAIL          = 3,
  SFX_TOO_SLOW      = 4,
  SFX_WRONG_MOVE    = 5,
  SFX_YOUR_SCORE    = 6,
  SFX_HIGH_SCORE    = 7,
  SFX_INTRO         = 8,
  SFX_NEXT_ONE      = 9,   // input test mode only
  SFX_THAT_WAS      = 10,
  SFX_SKIPPING      = 11,
  SFX_TEST_COMPLETE = 12,
};

// Order here = file order in /01 (001 = first entry).
enum CommandId : uint8_t {
  CMD_DROP_BEAT,    // button A
  CMD_AIR_HORN,     // button B
  CMD_FLIP_IT,      // switch
  CMD_SPIN_RIGHT,   // encoder
  CMD_SPIN_LEFT,
  CMD_VOLUME_UP,    // slider A
  CMD_VOLUME_DOWN,
  CMD_TEMPO_UP,     // slider B
  CMD_TEMPO_DOWN,
  NUM_COMMANDS,
  CMD_NONE = 255
};

const char *const COMMAND_NAMES[NUM_COMMANDS] = {
  "Drop the beat", "Air horn", "Flip it", "Spin right", "Spin left",
  "Volume up", "Volume down", "Tempo up", "Tempo down"
};

// ------------------------------- State ----------------------------------

SoftwareSerial mp3Serial(PIN_MP3_RX, PIN_MP3_TX);
DFRobotDFPlayerMini mp3;

// Debounces any small integer reading (a button, or the 3-position switch).
struct Debouncer {
  uint8_t stable;
  uint8_t lastRaw;
  unsigned long changedAt;

  void reset(uint8_t raw) {
    stable = lastRaw = raw;
    changedAt = millis();
  }

  // Returns true once when the stable value changes.
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
int sliderABase, sliderBBase;

volatile int encoderSteps = 0;
volatile uint8_t encoderState = 0;

int highScore = 0;
uint8_t lastCommand = CMD_NONE;

// ------------------------------- Inputs ---------------------------------

// Quadrature decoder: runs on every edge of CLK or DT, ignores contact bounce.
void encoderISR() {
  static const int8_t table[16] = {0, -1, 1, 0, 1, 0, 0, -1, -1, 0, 0, 1, 0, 1, -1, 0};
  encoderState = ((encoderState << 2) | (digitalRead(PIN_ENC_CLK) << 1) | digitalRead(PIN_ENC_DT)) & 0x0F;
  encoderSteps += table[encoderState];
}

int takeEncoderSteps() {
  noInterrupts();
  int steps = encoderSteps;
  interrupts();
  return ENCODER_REVERSE ? -steps : steps;
}

void clearEncoder() {
  noInterrupts();
  encoderSteps = 0;
  interrupts();
}

bool pressed(uint8_t pin) { return digitalRead(pin) == LOW; }

// 0 = side 1, 1 = side 2, 2 = neither (middle / between contacts).
uint8_t readSwitch() {
  if (pressed(PIN_SW_1)) return 0;
  if (pressed(PIN_SW_2)) return 1;
  return 2;
}

int readSlider(uint8_t pin, bool reverse) {
  analogRead(pin);  // first read after switching pins is stale, discard it
  delayMicroseconds(100);
  long sum = 0;
  for (uint8_t i = 0; i < 8; i++) sum += analogRead(pin);
  int value = sum / 8;
  return reverse ? 1023 - value : value;
}

// Treat the current position of every control as the new starting point,
// so anything the player did while audio was playing doesn't count.
void resetInputs() {
  buttonA.reset(pressed(PIN_BTN_A));
  buttonB.reset(pressed(PIN_BTN_B));
  switchPos.reset(readSwitch());
  sliderABase = readSlider(PIN_SLIDER_A, SLIDER_A_REVERSE);
  sliderBBase = readSlider(PIN_SLIDER_B, SLIDER_B_REVERSE);
  clearEncoder();
}

// Returns the command the player just performed, or CMD_NONE.
uint8_t detectInput() {
  if (buttonA.update(pressed(PIN_BTN_A)) && buttonA.stable) return CMD_DROP_BEAT;
  if (buttonB.update(pressed(PIN_BTN_B)) && buttonB.stable) return CMD_AIR_HORN;
  if (switchPos.update(readSwitch())) return CMD_FLIP_IT;

  int steps = takeEncoderSteps();
  if (steps >= SPIN_STEPS) return CMD_SPIN_RIGHT;
  if (steps <= -SPIN_STEPS) return CMD_SPIN_LEFT;

  int a = readSlider(PIN_SLIDER_A, SLIDER_A_REVERSE) - sliderABase;
  if (a >= SLIDE_THRESHOLD_A) return CMD_VOLUME_UP;
  if (a <= -SLIDE_THRESHOLD_A) return CMD_VOLUME_DOWN;

  int b = readSlider(PIN_SLIDER_B, SLIDER_B_REVERSE) - sliderBBase;
  if (b >= SLIDE_THRESHOLD_B) return CMD_TEMPO_UP;
  if (b <= -SLIDE_THRESHOLD_B) return CMD_TEMPO_DOWN;

  return CMD_NONE;
}

// -------------------------------- Audio ---------------------------------

void play(uint8_t folder, uint8_t file) {
#if LAPTOP_AUDIO
  // laptop_audio.py watches for these lines and plays SD_CARD/<folder>/<file>
  Serial.print(F("PLAY "));
  Serial.print(folder);
  Serial.print(' ');
  Serial.println(file);
#else
  mp3.playFolder(folder, file);
#endif
}

// Plays a clip and blocks until it is done (BUSY pin) or for fallbackMs.
void playAndWait(uint8_t folder, uint8_t file, unsigned long fallbackMs) {
  play(folder, file);
  if (BUSY_PIN < 0 || LAPTOP_AUDIO) {
    delay(fallbackMs);
    return;
  }
  // BUSY is LOW while playing. Wait for it to start, then to finish.
  unsigned long start = millis();
  while (digitalRead(BUSY_PIN) == HIGH && millis() - start < 500) {}
  while (digitalRead(BUSY_PIN) == LOW && millis() - start < 10000) {}
}

// --------------------------------- Game ---------------------------------

unsigned long timeLimitFor(int score) {
  unsigned long cut = (unsigned long)score * TIME_LIMIT_STEP_MS;
  if (cut > TIME_LIMIT_START_MS - TIME_LIMIT_MIN_MS) return TIME_LIMIT_MIN_MS;
  return TIME_LIMIT_START_MS - cut;
}

// Random command, never the same twice in a row, and never asks a slider to
// go up when it's already at the top (or down when it's at the bottom).
uint8_t pickCommand() {
  const int edgeA = SLIDE_THRESHOLD_A + 30;
  const int edgeB = SLIDE_THRESHOLD_B + 30;
  uint8_t cmd;
  do {
    cmd = random(NUM_COMMANDS);
    if (cmd == CMD_VOLUME_UP && sliderABase > 1023 - edgeA) cmd = CMD_VOLUME_DOWN;
    else if (cmd == CMD_VOLUME_DOWN && sliderABase < edgeA) cmd = CMD_VOLUME_UP;
    else if (cmd == CMD_TEMPO_UP && sliderBBase > 1023 - edgeB) cmd = CMD_TEMPO_DOWN;
    else if (cmd == CMD_TEMPO_DOWN && sliderBBase < edgeB) cmd = CMD_TEMPO_UP;
  } while (cmd == lastCommand);
  lastCommand = cmd;
  return cmd;
}

void waitForStart() {
  Serial.println(F("\nPress a button to play."));
  playAndWait(FOLDER_SFX, SFX_PRESS_TO_PLAY, WAIT_VOICE_MS);
  resetInputs();
  uint8_t input;
  do {
    input = detectInput();
  } while (input != CMD_DROP_BEAT && input != CMD_AIR_HORN);
  randomSeed(micros());  // human reaction time makes a good random seed
}

void gameOver(int score, bool timedOut) {
  Serial.print(timedOut ? F("Too slow! ") : F("Wrong move! "));
  Serial.print(F("Score: "));
  Serial.println(score);

  playAndWait(FOLDER_SFX, SFX_FAIL, WAIT_SUCCESS_MS);
  playAndWait(FOLDER_SFX, timedOut ? SFX_TOO_SLOW : SFX_WRONG_MOVE, WAIT_VOICE_MS);
  playAndWait(FOLDER_SFX, SFX_YOUR_SCORE, WAIT_VOICE_MS);
  if (score <= MAX_SCORE_CLIP) playAndWait(FOLDER_NUMBERS, score + 1, WAIT_NUMBER_MS);

  if (score > highScore) {
    highScore = score;
    Serial.println(F("New high score!"));
    playAndWait(FOLDER_SFX, SFX_HIGH_SCORE, WAIT_VOICE_MS);
  }
  Serial.print(F("High score: "));
  Serial.println(highScore);
}

void playGame() {
  int score = 0;
  lastCommand = CMD_NONE;
  playAndWait(FOLDER_SFX, SFX_INTRO, WAIT_INTRO_MS);

  while (true) {
    resetInputs();
    uint8_t cmd = pickCommand();
    unsigned long limit = timeLimitFor(score);
    Serial.print(F("> "));
    Serial.print(COMMAND_NAMES[cmd]);
    Serial.print(F("  ("));
    Serial.print(limit);
    Serial.println(F(" ms)"));

    // Timer starts with the voice line, so players can react as soon as they hear it.
    play(FOLDER_COMMANDS, cmd + 1);
    unsigned long start = millis();
    uint8_t input = CMD_NONE;
    while (input == CMD_NONE && millis() - start < limit) {
      input = detectInput();
    }

    if (input != cmd) {
      if (input != CMD_NONE) {
        Serial.print(F("  got: "));
        Serial.println(COMMAND_NAMES[input]);
      }
      gameOver(score, input == CMD_NONE);
      return;
    }

    score++;
    playAndWait(FOLDER_SFX, SFX_SUCCESS, WAIT_SUCCESS_MS);
  }
}

// ---------------------------- Input test ------------------------------

// Guided test: asks for each command by voice, in order, until all are done.
const unsigned long TEST_REMIND_MS = 6000;  // repeat the prompt after this long
const uint8_t TEST_MAX_REMINDERS   = 3;     // then skip it as broken

bool testDone[NUM_COMMANDS];
bool testFailed[NUM_COMMANDS];
uint8_t testTarget = CMD_NONE;
uint8_t testReminders = 0;
unsigned long testPromptAt = 0;
bool testFinished = false;

// A slider can't go "up" if it's already at the top, so ask for the other direction first.
bool testPossible(uint8_t cmd) {
  const int edgeA = SLIDE_THRESHOLD_A + 30;
  const int edgeB = SLIDE_THRESHOLD_B + 30;
  int a = readSlider(PIN_SLIDER_A, SLIDER_A_REVERSE);
  int b = readSlider(PIN_SLIDER_B, SLIDER_B_REVERSE);
  switch (cmd) {
    case CMD_VOLUME_UP:   return a <= 1023 - edgeA;
    case CMD_VOLUME_DOWN: return a >= edgeA;
    case CMD_TEMPO_UP:    return b <= 1023 - edgeB;
    case CMD_TEMPO_DOWN:  return b >= edgeB;
    default:              return true;
  }
}

uint8_t nextTestTarget() {
  uint8_t fallback = CMD_NONE;
  for (uint8_t i = 0; i < NUM_COMMANDS; i++) {
    if (testDone[i]) continue;
    if (testPossible(i)) return i;
    if (fallback == CMD_NONE) fallback = i;
  }
  return fallback;
}

void announceTestTarget() {
  Serial.print(F("Now do: "));
  Serial.println(COMMAND_NAMES[testTarget]);
  playAndWait(FOLDER_SFX, SFX_NEXT_ONE, WAIT_SHORT_MS);
  playAndWait(FOLDER_COMMANDS, testTarget + 1, WAIT_VOICE_MS);
  resetInputs();
  testPromptAt = millis();
}

void printRawInputs() {
  Serial.print(F("   raw: btnA=")); Serial.print(pressed(PIN_BTN_A));
  Serial.print(F(" btnB=")); Serial.print(pressed(PIN_BTN_B));
  Serial.print(F(" switch=")); Serial.print(readSwitch());
  Serial.print(F(" enc=")); Serial.print(takeEncoderSteps());
  Serial.print(F(" A0=")); Serial.print(readSlider(PIN_SLIDER_A, SLIDER_A_REVERSE));
  Serial.print(F(" A1=")); Serial.println(readSlider(PIN_SLIDER_B, SLIDER_B_REVERSE));
}

void finishTest() {
  testFinished = true;
  Serial.println(F("\n==== Input test complete ===="));
  bool allOk = true;
  for (uint8_t i = 0; i < NUM_COMMANDS; i++) {
    Serial.print(testFailed[i] ? F("  FAILED  ") : F("  ok      "));
    Serial.println(COMMAND_NAMES[i]);
    if (testFailed[i]) allOk = false;
  }
  Serial.println(allOk ? F("All inputs working!") : F("Check the wiring of the FAILED inputs."));
  Serial.println(F("Press the Nano's reset button to run the test again."));
  playAndWait(FOLDER_SFX, SFX_TEST_COMPLETE, WAIT_VOICE_MS);
}

void inputTestLoop() {
  if (testFinished) return;

  if (testTarget == CMD_NONE) {
    testTarget = nextTestTarget();
    if (testTarget == CMD_NONE) {
      finishTest();
      return;
    }
    testReminders = 0;
    announceTestTarget();
  }

  uint8_t input = detectInput();
  if (input == testTarget) {
    Serial.println(F("  correct!"));
    testDone[testTarget] = true;
    playAndWait(FOLDER_SFX, SFX_SUCCESS, WAIT_SUCCESS_MS);
    testTarget = CMD_NONE;
  } else if (input != CMD_NONE) {
    // Say what it actually detected, e.g. "That was... Tempo down!"
    Serial.print(F("  that was: "));
    Serial.println(COMMAND_NAMES[input]);
    playAndWait(FOLDER_SFX, SFX_THAT_WAS, WAIT_SHORT_MS);
    playAndWait(FOLDER_COMMANDS, input + 1, WAIT_VOICE_MS);
    announceTestTarget();
  } else if (millis() - testPromptAt >= TEST_REMIND_MS) {
    printRawInputs();
    if (++testReminders > TEST_MAX_REMINDERS) {
      Serial.print(F("  skipping (no response): "));
      Serial.println(COMMAND_NAMES[testTarget]);
      testDone[testTarget] = testFailed[testTarget] = true;
      playAndWait(FOLDER_SFX, SFX_SKIPPING, WAIT_LONG_MS);
      testTarget = CMD_NONE;
    } else {
      announceTestTarget();
    }
  }
}

// -------------------------------- Setup ---------------------------------

void setup() {
  Serial.begin(115200);

  pinMode(PIN_ENC_CLK, INPUT_PULLUP);
  pinMode(PIN_ENC_DT, INPUT_PULLUP);
  pinMode(PIN_BTN_A, INPUT_PULLUP);
  pinMode(PIN_BTN_B, INPUT_PULLUP);
  pinMode(PIN_SW_1, INPUT_PULLUP);
  pinMode(PIN_SW_2, INPUT_PULLUP);
  if (BUSY_PIN >= 0) pinMode(BUSY_PIN, INPUT);

  attachInterrupt(digitalPinToInterrupt(PIN_ENC_CLK), encoderISR, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_ENC_DT), encoderISR, CHANGE);

#if LAPTOP_AUDIO
  Serial.println(F("Laptop audio mode: DFPlayer not used."));
#else
  mp3Serial.begin(9600);
  Serial.println(F("Starting DFPlayer..."));
  // If this keeps failing on a clone module, try mp3.begin(mp3Serial, false).
  if (!mp3.begin(mp3Serial)) {
    Serial.println(F("DFPlayer not found: check RX/TX wiring, 5V power and the SD card."));
  }
  delay(500);
  mp3.volume(MP3_VOLUME);
#endif

  resetInputs();
  Serial.println(F("DJ Bop-It ready."));
}

void loop() {
#if INPUT_TEST_MODE
  inputTestLoop();
#else
  waitForStart();
  playGame();
#endif
}
