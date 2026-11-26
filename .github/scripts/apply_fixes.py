#!/usr/bin/env python3
"""Aplica correcciones minimas sobre los archivos ACTUALES del fork (no los reemplaza).

Cada parche busca un fragmento (ignorando diferencias de espacios) y lo sustituye.
Si el fragmento no existe en tu rama, ese parche se omite con un aviso y el build sigue.
Es idempotente: si ya se aplico, no hace nada.
"""
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
applied, skipped, missing = [], [], []


def flex(snippet):
    """Regex que ignora diferencias de espacios/saltos de linea entre tokens."""
    return r"\s*".join(re.escape(tok) for tok in snippet.split())


def find_file(suffix):
    hits = [p for p in ROOT.rglob("*") if p.is_file() and p.as_posix().endswith(suffix)
            and "/build/" not in p.as_posix() and "/.cxx/" not in p.as_posix()]
    return hits[0] if hits else None


def patch(suffix, label, old, new, marker, count=1, regex=False, requires=None):
    path = find_file(suffix)
    if path is None:
        missing.append(f"{label}: no existe {suffix}")
        return
    text = path.read_text(encoding="utf-8")
    if marker in text:
        skipped.append(f"{label}: ya aplicado")
        return
    if requires and requires not in text:
        missing.append(f"{label}: omitido porque falta el parche previo ({requires})")
        return
    pattern = old if regex else flex(old)
    m = re.search(pattern, text, re.S)
    if not m:
        missing.append(f"{label}: no se encontro el fragmento en {suffix} (tu rama es distinta)")
        return
    text = re.sub(pattern, lambda _m: new, text, count=count, flags=re.S)
    path.write_text(text, encoding="utf-8")
    applied.append(label)


# --------------------------------------------------------------------------- MainActivity.kt
patch(
    "ui/main/MainActivity.kt", "Carpeta: permiso URI sin crash",
    old="""val contentResolver = contentResolver
        val canonicalizedUri = contentResolver.canonicalize(uri)
        if (canonicalizedUri != null) uri = canonicalizedUri

        val takeFlags = result.flags and Intent.FLAG_GRANT_READ_URI_PERMISSION
        getContentResolver().takePersistableUriPermission(uri, takeFlags)""",
    new="""// [fix] Persist the grant on the exact URI the picker returned. canonicalize() could turn
        // a tree URI into a document URI we hold no grant for -> SecurityException -> crash.
        try {
            contentResolver.takePersistableUriPermission(
                uri!!, Intent.FLAG_GRANT_READ_URI_PERMISSION
            )
        } catch (e: SecurityException) {
            org.dolphinemu.dolphinemu.utils.Log.warning(
                "[MainActivity] Could not persist permission for $uri: ${e.message}"
            )
        } catch (e: Exception) {
            org.dolphinemu.dolphinemu.utils.Log.error(
                "[MainActivity] takePersistableUriPermission failed: ${e.message}"
            )
            android.widget.Toast.makeText(
                this, org.dolphinemu.dolphinemu.R.string.write_permission_needed,
                android.widget.Toast.LENGTH_LONG
            ).show()
            return
        }""",
    marker="[fix] Persist the grant",
)
patch(
    "ui/main/MainActivity.kt", "MainActivity: listFiles() nulo",
    old="for (f in dir.listFiles()) {",
    new="for (f in dir.listFiles() ?: emptyArray()) { // [fix-null]",
    marker="[fix-null]", count=0,
)

# --------------------------------------------------------------------------- GameFileCache.kt
patch(
    "model/GameFileCache.kt", "Cache: copia del set en scanLibrary",
    old="""val folderPathsSet =
            preferences.getStringSet(GAME_FOLDER_PATHS_PREFERENCE, HashSet())!!""",
    new="""// [fix-copy] getStringSet() returns the live set; never mutate it, work on a copy.
        val folderPathsSet: MutableSet<String> = HashSet(
            preferences.getStringSet(GAME_FOLDER_PATHS_PREFERENCE, HashSet()) ?: HashSet()
        )""",
    marker="[fix-copy]",
)
patch(
    "model/GameFileCache.kt", "Cache: escanear solo carpetas existentes",
    old="val folderPaths = folderPathsSet.toTypedArray<String>()",
    new="val folderPaths = newFolderPaths.toTypedArray<String>() // [fix-existing]",
    marker="[fix-existing]",
)
patch(
    "model/GameFileCache.kt", "Cache: copia del set en addGameFolder",
    old="""val folderPaths =
                preferences.getStringSet(GAME_FOLDER_PATHS_PREFERENCE, HashSet())!!
            if (!folderPaths.contains(path)) {""",
    new="""val folderPaths: MutableSet<String> = HashSet( // [fix-copy2]
                preferences.getStringSet(GAME_FOLDER_PATHS_PREFERENCE, HashSet()) ?: HashSet()
            )
            if (!folderPaths.contains(path)) {""",
    marker="[fix-copy2]",
)

# --------------------------------------------------------------------------- GameFileCacheService.java
patch(
    "services/GameFileCacheService.java", "Servicio: addOrGet sin NPE",
    old="""synchronized (gameFileCache)
    {
      return gameFileCache.addOrGet(gamePath);
    }""",
    new="""if (gameFileCache == null) // [fix-npe]
      return null;

    synchronized (gameFileCache)
    {
      return gameFileCache.addOrGet(gamePath);
    }""",
    marker="[fix-npe]",
)

# --------------------------------------------------------------------------- EmulationFragment.kt
patch(
    "fragments/EmulationFragment.kt", "Emulacion: Surface valida + cerrar si falla el boot",
    old="""Thread({
                  NativeLibrary.SurfaceChanged(surface)
                  NativeLibrary.Run(mGamePaths, statePath)
                }, "NativeEmulation").start()""",
    new="""// [fix-surface] Capture the Surface now; the field may be nulled before the thread runs.
                val startSurface = surface
                if (startSurface == null || !startSurface.isValid) {
                    runWhenSurfaceIsValid = true
                    return
                }
                Thread({
                  NativeLibrary.SurfaceChanged(startSurface)
                  NativeLibrary.Run(mGamePaths, statePath)

                  // Run() returned without the user asking (boot failure / game ended):
                  // close the screen instead of leaving it black.
                  val activity = EmulationActivity.get()
                  if (activity != null && state != State.STOPPED) {
                    activity.runOnUiThread {
                      if (!activity.isFinishing) activity.exitEmulation()
                    }
                  }
                }, "NativeEmulation").start()""",
    marker="[fix-surface]",
)

# --------------------------------------------------------------------------- GameFileCache.cpp (JNI)
patch(
    "jni/GameList/GameFileCache.cpp", "JNI: getAllGames sin overflow de referencias",
    old="""const jobjectArray array = env->NewObjectArray(static_cast<jsize>(g_game_file_cache->GetSize()),
                                                 IDCache::sGameFile.Clazz, nullptr);
  jsize i = 0;
  g_game_file_cache->ForEach([env, array, &i](const auto& game_file) {
    env->SetObjectArrayElement(array, i++, GameFileToJava(env, game_file.get()));
  });
  return array;""",
    new="""// [fix-jni]
  if (!g_game_file_cache)
    return env->NewObjectArray(0, IDCache::sGameFile.Clazz, nullptr);

  const jsize size = static_cast<jsize>(g_game_file_cache->GetSize());
  const jobjectArray array = env->NewObjectArray(size, IDCache::sGameFile.Clazz, nullptr);
  jsize i = 0;
  g_game_file_cache->ForEach([env, array, size, &i](const auto& game_file) {
    if (i >= size)
      return;
    jobject java_game = GameFileToJava(env, game_file.get());
    env->SetObjectArrayElement(array, i++, java_game);
    // Without this, big libraries overflow the JNI local reference table.
    if (java_game)
      env->DeleteLocalRef(java_game);
  });
  return array;""",
    marker="[fix-jni]",
)
patch(
    "jni/GameList/GameFileCache.cpp", "JNI: addOrGet con cache nula",
    old="""bool cache_changed = false;
  return GameFileToJava(env,""",
    new="""if (!g_game_file_cache) // [fix-jni2]
    return nullptr;

  bool cache_changed = false;
  return GameFileToJava(env,""",
    marker="[fix-jni2]",
)

# --------------------------------------------------------------------------- MainAndroid.cpp (JNI)
patch(
    "jni/MainAndroid.cpp", "JNI: BootGame con boot nulo",
    old="""std::unique_ptr<BootParameters> boot = BootParameters::GenerateFromFile(paths, savestate);
  boot->delete_savestate = delete_savestate;
  WindowSystemInfo wsi(WindowSystemType::Android, nullptr, s_surf);
  wsi.render_surface_scale = s_scaled_density;
  if (BootManager::BootCore(std::move(boot), wsi))
  {""",
    new="""std::unique_ptr<BootParameters> boot = BootParameters::GenerateFromFile(paths, savestate);
  // [fix-boot] GenerateFromFile() returns nullptr when the file can't be opened; it used to be
  // dereferenced unchecked (crash).
  bool boot_attempted = false;
  bool boot_ok = false;
  if (boot)
  {
    boot_attempted = true;
    boot->delete_savestate = delete_savestate;
    WindowSystemInfo wsi(WindowSystemType::Android, nullptr, s_surf);
    wsi.render_surface_scale = s_scaled_density;
    boot_ok = BootManager::BootCore(std::move(boot), wsi);
  }

  if (boot_ok)
  {""",
    marker="[fix-boot]",
)
patch(
    "jni/MainAndroid.cpp", "JNI: avisar si el juego no arranca",
    old="""Core::Shutdown();
  SConfig::GetInstance().SaveSettings();
  ButtonManager::Shutdown();""",
    new="""if (!boot_ok)  // [fix-boot2]
  {
    __android_log_print(ANDROID_LOG_ERROR, DOLPHIN_TAG, "Failed to boot the selected game.");
    guard.unlock();
    MsgAlert("Dolphin",
             "The game could not be started. Check that the file is readable and that the "
             "folder permission was granted, then try again.",
             false, Common::MsgType::Critical);
    guard.lock();
  }

  if (boot_attempted)
    Core::Shutdown();
  SConfig::GetInstance().SaveSettings();
  ButtonManager::Shutdown();""",
    marker="[fix-boot2]", requires="[fix-boot]",
)
patch(
    "jni/MainAndroid.cpp", "JNI: SurfaceChanged con Surface nula",
    old="""s_surf = ANativeWindow_fromSurface(env, surf);
  if (s_surf == nullptr)""",
    new="""ANativeWindow* old_surf = s_surf;  // [fix-surf]
  s_surf = surf ? ANativeWindow_fromSurface(env, surf) : nullptr;
  if (s_surf == nullptr)""",
    marker="[fix-surf]",
)
patch(
    "jni/MainAndroid.cpp", "JNI: liberar Surface anterior",
    old="""g_renderer->ChangeSurface(s_surf);
}

JNIEXPORT void JNICALL Java_org_dolphinemu_dolphinemu_NativeLibrary_SurfaceDestroyed""",
    new="""g_renderer->ChangeSurface(s_surf);

  // [fix-surf2] release the previous window only after the renderer switched away from it
  if (old_surf && old_surf != s_surf)
    ANativeWindow_release(old_surf);
}

JNIEXPORT void JNICALL Java_org_dolphinemu_dolphinemu_NativeLibrary_SurfaceDestroyed""",
    marker="[fix-surf2]", requires="[fix-surf]",
)

# --------------------------------------------------------------------------- Core.cpp (afinidad big cores)
CORE_HELPER = r'''
#ifdef __ANDROID__  // [fix-affinity]
// Heterogeneous SoCs (e.g. Unisoc T700 in the Moto G20: 2x Cortex-A75 + 6x Cortex-A55) let the
// scheduler park the CPU/GPU emulation threads on slow cores -> random FPS drops. Both clusters
// report the same max frequency on that chip, so cores are classified by ARM "CPU part".
static bool IsLittleCorePart(unsigned part)
{
  switch (part)
  {
  case 0xd03:  // A53
  case 0xd04:  // A35
  case 0xd05:  // A55
  case 0xd46:  // A510
  case 0xd80:  // A520
    return true;
  default:
    return false;
  }
}

static const std::vector<int>& GetBigCores()
{
  static const std::vector<int> big_cores = [] {
    std::vector<unsigned> parts;
    if (FILE* f = fopen("/proc/cpuinfo", "r"))
    {
      char line[256];
      while (fgets(line, sizeof(line), f))
      {
        unsigned part = 0;
        if (sscanf(line, "CPU part : %x", &part) == 1)
          parts.push_back(part);
      }
      fclose(f);
    }

    std::vector<int> big;
    const int n = static_cast<int>(parts.size());
    for (int i = 0; i < n; ++i)
    {
      if (!IsLittleCorePart(parts[i]))
        big.push_back(i);
    }

    // Homogeneous CPU, or a single big core: pinning would not help (or would hurt).
    if (big.size() < 2 || static_cast<int>(big.size()) == n)
      big.clear();
    return big;
  }();
  return big_cores;
}

static void PinCurrentThreadToBigCores()
{
  const std::vector<int>& big = GetBigCores();
  if (big.empty())
    return;

  cpu_set_t set;
  CPU_ZERO(&set);
  for (const int core : big)
    CPU_SET(core, &set);
  sched_setaffinity(0, sizeof(set), &set);  // pid 0 == calling thread; failure is harmless
}
#else
static void PinCurrentThreadToBigCores()
{
}
#endif

'''
patch(
    "Core/Core/Core.cpp", "Core: includes de afinidad",
    old="#include <variant>\n",
    new="#include <variant>\n#ifdef __ANDROID__\n#include <sched.h>\n#include <cstdio>\n#include <vector>\n#endif  // [fix-affinity-inc]\n",
    marker="[fix-affinity-inc]",
)
patch(
    "Core/Core/Core.cpp", "Core: helper de afinidad",
    old=r"// Create the CPU thread, which is a CPU + Video thread in Single Core mode.",
    new=CORE_HELPER + "// Create the CPU thread, which is a CPU + Video thread in Single Core mode.",
    marker="[fix-affinity]",
)
patch(
    "Core/Core/Core.cpp", "Core: fijar hilo de CPU",
    old='Common::SetCurrentThreadName("CPU-GPU thread");',
    new='Common::SetCurrentThreadName("CPU-GPU thread");\n  PinCurrentThreadToBigCores();  // [fix-pin1]',
    marker="[fix-pin1]", requires="[fix-affinity]",
)
patch(
    "Core/Core/Core.cpp", "Core: fijar hilo de video",
    old='Common::SetCurrentThreadName("Video thread");',
    new='Common::SetCurrentThreadName("Video thread");\n    PinCurrentThreadToBigCores();  // [fix-pin2]',
    marker="[fix-pin2]", requires="[fix-affinity]",
)

# --------------------------------------------------------------------------- GraphicsSettings.cpp
patch(
    "Core/Core/Config/GraphicsSettings.cpp", "Shaders asincronos por defecto",
    old='"ShaderCompilationMode"}, ShaderCompilationMode::Synchronous}',
    new='"ShaderCompilationMode"},\n    ShaderCompilationMode::AsynchronousSkipRendering}  /* [fix-shader] */',
    marker="[fix-shader]",
)

# --------------------------------------------------------------------------- app/build.gradle
patch(
    "Android/app/build.gradle", "Gradle: -Ofast -> -O3",
    old='"-DCMAKE_CXX_FLAGS_RELEASE=-Ofast"',
    new='"-DCMAKE_BUILD_TYPE=Release", // [fix-flags]\n'
        '                    "-DCMAKE_C_FLAGS_RELEASE=-O3 -fno-math-errno -fno-trapping-math -DNDEBUG",\n'
        '                    "-DCMAKE_CXX_FLAGS_RELEASE=-O3 -fno-math-errno -fno-trapping-math -DNDEBUG"',
    marker="[fix-flags]",
)
patch(
    "Android/app/build.gradle", "Gradle: firma con fallback a debug",
    old=r"signingConfig\s+signingConfigs\.release\b(?!\s*\{)",
    new="signingConfig (signingConfigs.release.storeFile?.exists() ? signingConfigs.release : signingConfigs.debug) // [fix-sign]",
    marker="[fix-sign]", count=0, regex=True,
)

# --------------------------------------------------------------------------- resumen
print("\n=== Resumen de correcciones ===")
for a in applied:
    print(f"  [OK]      {a}")
for s in skipped:
    print(f"  [=]       {s}")
for m in missing:
    print(f"  [OMITIDO] {m}")
    print(f"::warning::{m}")
