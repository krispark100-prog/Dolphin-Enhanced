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
applied, skipped, missing, diag = [], [], [], []

import subprocess

# --------------------------------------------------------------------------- FASE 0
# Si el fork aun contiene archivos COMPLETOS del paquete anterior (v1), se restauran desde el
# historial de git a la version que tenia tu rama justo antes de subirlos. Asi no hay que
# revertir nada a mano. Cada marcador es un texto que solo existe en los archivos v1.
V1_MARKERS = {
    "Android/app/build.gradle": "which breaks the IEEE-754 behaviour",
    "ui/main/MainActivity.kt": "We only ever read games",
    "model/GameFileCache.kt": "Scan only folders that still exist",
    "services/GameFileCacheService.java": "final GameFileCache cache = gameFileCache;",
    "fragments/EmulationFragment.kt": "Capture the Surface now: the field can be nulled",
    "jni/GameList/GameFileCache.cpp": "big libraries overflow the JNI local reference table and the app aborts",
    "jni/MainAndroid.cpp": "Failed to boot the selected game.",
    "Core/Core/Core.cpp": "IsLittleCorePart",
    "Core/Core/Config/GraphicsSettings.cpp":
        '"ShaderCompilationMode"},\n    ShaderCompilationMode::AsynchronousSkipRendering}',
}
restored, restore_errors = [], []


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd or ROOT, capture_output=True, text=True)


def restore_v1_files():
    top = git("rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return
    top = Path(top.stdout.strip())
    for suffix, marker in V1_MARKERS.items():
        path = None
        for p in ROOT.rglob("*"):
            if p.is_file() and p.as_posix().endswith(suffix) and "/build/" not in p.as_posix() \
                    and "/.cxx/" not in p.as_posix():
                path = p
                break
        if path is None:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if marker not in text or "[fix" in text:
            continue
        rel = path.relative_to(top).as_posix()
        # ultimo commit que cambio la cantidad de apariciones del marcador (= el que lo agrego)
        log = git("log", "-1", "--format=%H", f"-S{marker}", "--", rel, cwd=top)
        sha = log.stdout.strip()
        if not sha:
            restore_errors.append(f"{rel}: no se encontro el commit que lo cambio (historial incompleto)")
            continue
        old = git("show", f"{sha}^:{rel}", cwd=top)
        if old.returncode != 0:
            has_parent = git("rev-parse", "--verify", "-q", f"{sha}^", cwd=top).returncode == 0
            if has_parent:
                # El paquete anterior CREO este archivo (antes no existia en esa ruta): puede estar
                # tapando al original (p. ej. build.gradle junto a build.gradle.kts). Se elimina.
                path.unlink()
                restored.append(f"{rel} (archivo nuevo creado por el paquete anterior: eliminado)")
            else:
                restore_errors.append(f"{rel}: el commit {sha[:8]} no tiene version anterior")
            continue
        path.write_text(old.stdout, encoding="utf-8", newline="")
        restored.append(f"{rel} (version previa a {sha[:8]})")


restore_v1_files()

if "--restore-only" in sys.argv:
    print("\n=== Restauracion de archivos del paquete anterior (solo restaurar) ===")
    if not restored and not restore_errors:
        print("  (nada que restaurar: el repo ya esta limpio)")
    for r in restored:
        print(f"  [RESTAURADO] {r}")
    for e in restore_errors:
        print(f"  [ERROR]      {e}")
        print(f"::error::{e}")
    sys.exit(1 if restore_errors else 0)



def flex(snippet):
    """Regex que ignora diferencias de espacios/saltos de linea entre tokens."""
    return r"\s*".join(re.escape(tok) for tok in snippet.split())


def find_file(suffix):
    hits = [p for p in ROOT.rglob("*") if p.is_file() and p.as_posix().endswith(suffix)
            and "/build/" not in p.as_posix() and "/.cxx/" not in p.as_posix()]
    return hits[0] if hits else None


def patch(suffix, label, old, new, marker, count=1, regex=False, requires=None, done=None, hint=None, quiet=False, keep=False):
    path = find_file(suffix)
    if path is None:
        if not quiet:
            missing.append(f"{label}: no existe {suffix}")
        return False
    text = path.read_text(encoding="utf-8")
    if marker in text:
        skipped.append(f"{label}: ya aplicado")
        return True
    if done and re.search(flex(done), text, re.S):
        skipped.append(f"{label}: ya estaba asi")
        return True
    if requires and requires not in text:
        missing.append(f"{label}: omitido porque falta el parche previo ({requires})")
        return False
    pattern = old if regex else flex(old)
    m = re.search(pattern, text, re.S)
    if not m:
        if quiet:
            return False
        missing.append(f"{label}: no se encontro el fragmento en {suffix} (tu rama es distinta)")
        if hint:
            lines = text.splitlines()
            for i, ln in enumerate(lines):
                if re.search(hint, ln):
                    lo, hi = max(0, i - 4), min(len(lines), i + 10)
                    diag.append(f"--- {suffix} (lineas {lo + 1}-{hi}) ---\n" + "\n".join(lines[lo:hi]))
                    break
        return False
    text = re.sub(pattern, (lambda _m: _m.group(0) + new) if keep else (lambda _m: new),
                  text, count=count, flags=re.S)
    path.write_text(text, encoding="utf-8")
    applied.append(label)
    return True




def patch_any(suffix, label, variants, marker, **kw):
    """Prueba varias variantes (old, new) hasta que una encaje."""
    for i, (old, new) in enumerate(variants):
        last = i == len(variants) - 1
        if patch(suffix, label, old, new, marker, quiet=not last, **kw):
            return True
    return False


# --------------------------------------------------------------------------- MainActivity.kt
patch(
    "ui/main/MainActivity.kt", "Carpeta: permiso URI sin crash",
    old="""val contentResolver = contentResolver
        val canonicalizedUri = contentResolver.canonicalize(uri)
        if (canonicalizedUri != null) uri = canonicalizedUri

        val takeFlags = result.flags and Intent.FLAG_GRANT_READ_URI_PERMISSION
        getContentResolver().takePersistableUriPermission(uri, takeFlags)""",
    new="""org.dolphinemu.dolphinemu.utils.CrashLogger.log("onDirectorySelected uri=$uri")
        // [fix] Persist the grant on the exact URI the picker returned. canonicalize() could turn
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
patch_any(
    "model/GameFileCache.kt", "Cache: copia del set en scanLibrary",
    [
        # tu rama
        ("""val folderPathsSet = preferences.getStringSet(GAME_FOLDER_PATHS_PREFERENCE, HashSet())
            ?: return false""",
         """// [fix-copy] getStringSet() returns the live set owned by SharedPreferences; work on a copy.
        val folderPathsSet: MutableSet<String> = HashSet(
            preferences.getStringSet(GAME_FOLDER_PATHS_PREFERENCE, HashSet()) ?: return false
        )"""),
        # zip original
        ("""val folderPathsSet =
            preferences.getStringSet(GAME_FOLDER_PATHS_PREFERENCE, HashSet())!!""",
         """// [fix-copy] getStringSet() returns the live set owned by SharedPreferences; work on a copy.
        val folderPathsSet: MutableSet<String> = HashSet(
            preferences.getStringSet(GAME_FOLDER_PATHS_PREFERENCE, HashSet()) ?: HashSet()
        )"""),
    ],
    marker="[fix-copy]", hint=r"getStringSet",
)
patch(
    "model/GameFileCache.kt", "Cache: escanear solo carpetas existentes",
    old="val folderPaths = folderPathsSet.toTypedArray",
    new="val folderPaths = newFolderPaths.toTypedArray",
    marker="newFolderPaths.toTypedArray", hint=r"toTypedArray",
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
    done='"ShaderCompilationMode"}, ShaderCompilationMode::AsynchronousSkipRendering}',
)

# --------------------------------------------------------------------------- app/build.gradle
patch(
    "Android/app/build.gradle", "Gradle: -Ofast -> -O3",
    old='"-DCMAKE_CXX_FLAGS_RELEASE=-Ofast"',
    new='"-DCMAKE_BUILD_TYPE=Release", // [fix-flags]\n'
        '                    "-DCMAKE_C_FLAGS_RELEASE=-O3 -fno-math-errno -fno-trapping-math -DNDEBUG",\n'
        '                    "-DCMAKE_CXX_FLAGS_RELEASE=-O3 -fno-math-errno -fno-trapping-math -DNDEBUG"',
    marker="[fix-flags]", quiet=True,
)
patch(
    "Android/app/build.gradle", "Gradle: firma con fallback a debug",
    old=r"signingConfig\s+signingConfigs\.release\b(?!\s*\{)",
    new="signingConfig (signingConfigs.release.storeFile?.exists() ? signingConfigs.release : signingConfigs.debug) // [fix-sign]",
    marker="[fix-sign]", count=0, regex=True, quiet=True,
)


# =========================================================================== DIAGNOSTICO + CARPETA DE USUARIO
SRC_LOGGER = Path(__file__).parent / "crashlogger_src" / "CrashLogger.java"


def create_next_to(anchor_suffix, filename, content, label):
    anchor = find_file(anchor_suffix)
    if anchor is None:
        missing.append(f"{label}: no existe {anchor_suffix}")
        return
    dest = anchor.parent / filename
    if dest.exists():
        skipped.append(f"{label}: ya existe")
        return
    dest.write_text(content, encoding="utf-8")
    applied.append(label)


if SRC_LOGGER.exists():
    create_next_to("utils/DirectoryInitialization.java", "CrashLogger.java",
                   SRC_LOGGER.read_text(encoding="utf-8"), "Diagnostico: CrashLogger.java (archivo nuevo)")
else:
    missing.append("Diagnostico: falta .github/scripts/crashlogger_src/CrashLogger.java")

patch(
    "dolphinemu/DolphinApplication.java", "Diagnostico: instalar registro en Application",
    old="application = this;",
    new="application = this;\n    org.dolphinemu.dolphinemu.utils.CrashLogger.install(this);",
    marker="CrashLogger.install",
)
patch(
    "ui/main/MainActivity.kt", "Diagnostico: mostrar informe al abrir",
    old="super.onCreate(savedInstanceState)",
    new="super.onCreate(savedInstanceState)\n        org.dolphinemu.dolphinemu.utils.CrashLogger.showPendingReport(this)",
    marker="CrashLogger.showPendingReport", hint=r"override fun onCreate",
)
patch(
    "ui/main/MainActivity.kt", "Diagnostico: migas en onDirectorySelected",
    old=r"fun onDirectorySelected\([^)]*\)\s*\{",
    new='\n        org.dolphinemu.dolphinemu.utils.CrashLogger.log("onDirectorySelected iniciado")',
    marker='CrashLogger.log("onDirectorySelected', regex=True, keep=True, quiet=True,
)
patch(
    "model/GameFileCache.kt", "Diagnostico: migas en scanLibrary",
    old=r"fun scanLibrary\([^)]*\)[^{=]*\{",
    new='\n        org.dolphinemu.dolphinemu.utils.CrashLogger.log("scanLibrary iniciado")',
    marker='CrashLogger.log("scanLibrary', regex=True, keep=True, quiet=True,
)
patch(
    "model/GameFileCache.kt", "Diagnostico: migas en addGameFolder",
    old=r"fun addGameFolder\([^)]*\)[^{=]*\{",
    new='\n        org.dolphinemu.dolphinemu.utils.CrashLogger.log("addGameFolder: $path")',
    marker='CrashLogger.log("addGameFolder', regex=True, keep=True, quiet=True,
)

# Carpeta de usuario: respaldo si /sdcard/dolphin-mmj no se puede usar (causa probable de
# "ajustes no se guardan" y del crash al escanear: sin carpeta de usuario el nativo no se inicializa)
patch(
    "utils/DirectoryInitialization.java", "Carpeta de usuario: helper de comprobacion",
    old="private static boolean setDolphinUserDirectory(Context context)",
    new="""// [fix-userdir-helper]
  private static boolean isUsableDirectory(File dir)
  {
    try
    {
      if (!dir.isDirectory() && !dir.mkdirs())
        return false;
      File probe = new File(dir, ".write_test");
      if (!probe.exists() && !probe.createNewFile())
        return false;
      probe.delete();
      return true;
    }
    catch (Exception e)
    {
      return false;
    }
  }

  private static boolean setDolphinUserDirectory(Context context)""",
    marker="[fix-userdir-helper]",
)
patch(
    "utils/DirectoryInitialization.java", "Carpeta de usuario: respaldo en carpeta de la app",
    old="""File userPath = new File(externalPath, "dolphin-mmj");
    if (!userPath.isDirectory() && !userPath.mkdir())
    {
      return false;
    }""",
    new="""File userPath = new File(externalPath, "dolphin-mmj");
    if (!isUsableDirectory(userPath))  // [fix-userdir]
    {
      // /sdcard/dolphin-mmj is not writable on this device: fall back to the app-specific folder
      // so settings can be saved and the native side always gets a valid user directory.
      File appDir = context.getExternalFilesDir(null);
      if (appDir == null)
        appDir = context.getFilesDir();
      File fallback = new File(appDir, "dolphin-mmj");
      CrashLogger.log("No se pudo usar " + userPath + "; probando " + fallback);
      if (!isUsableDirectory(fallback))
      {
        CrashLogger.log("Tampoco se pudo usar " + fallback);
        return false;
      }
      userPath = fallback;
    }
    CrashLogger.log("Directorio de usuario: " + userPath);""",
    marker="[fix-userdir]", requires="[fix-userdir-helper]", hint=r"dolphin-mmj",
)

# Ajustes: crear carpeta, registrar ruta y errores de escritura
patch(
    "settings/utils/SettingsFile.java", "Ajustes: crear carpeta y registrar ruta",
    old='writer = new PrintWriter(ini, "UTF-8");',
    new="""ini.getParentFile().mkdirs();  // [fix-ini]
      org.dolphinemu.dolphinemu.utils.CrashLogger.log("Guardando ajustes en " + ini);
      writer = new PrintWriter(ini, "UTF-8");""",
    marker="[fix-ini]", hint=r"PrintWriter",
)
patch(
    "settings/utils/SettingsFile.java", "Ajustes: registrar error de escritura",
    old='Log.error("[SettingsFile] File not found: "',
    new=("org.dolphinemu.dolphinemu.utils.CrashLogger.log(\"Ajustes: no se pudo escribir \" + ini + \": \" + e.getMessage());\n"
         "      Log.error(\"[SettingsFile] File not found: \""),
    marker="Ajustes: no se pudo escribir",
)


for _suffix in ("app/build.gradle", "app/build.gradle.kts"):
    patch(
        _suffix, f"Gradle simple ({_suffix.split('/')[-1]}): -Ofast -> -O3",
        old="-DCMAKE_CXX_FLAGS_RELEASE=-Ofast",
        new="-DCMAKE_CXX_FLAGS_RELEASE=-O3 -fno-math-errno -fno-trapping-math -DNDEBUG",
        marker="-O3 -fno-math-errno -fno-trapping-math", quiet=True,
    )

# ---- listado de archivos de build (diagnostico)
_build_dir = None
for _p in ROOT.rglob("settings.gradle*"):
    if "/Source/Android/" in _p.as_posix() + "/" and "/build/" not in _p.as_posix():
        _build_dir = _p.parent
        break
if _build_dir:
    names = sorted(x.relative_to(_build_dir).as_posix() for x in list(_build_dir.glob("*.gradle*"))
                   + list(_build_dir.glob("app/*.gradle*")) + list(_build_dir.glob("gradle/*.toml")))
    diag.append("--- archivos de build en " + _build_dir.relative_to(ROOT).as_posix() + " ---\n" + "\n".join(names))
    for _f in _build_dir.glob("app/build.gradle*"):
        lines = [l.strip() for l in _f.read_text(encoding="utf-8", errors="replace").splitlines()
                 if re.search(r"coil|okhttp|picasso|CMAKE_(C|CXX)_FLAGS|signingConfig ", l)]
        diag.append(f"--- lineas relevantes de {_f.name} ---\n" + "\n".join(lines[:25]))

# --------------------------------------------------------------------------- resumen
print("\n=== Restauracion de archivos del paquete anterior ===")
if not restored and not restore_errors:
    print("  (nada que restaurar)")
for r in restored:
    print(f"  [RESTAURADO] {r}")
for e in restore_errors:
    print(f"  [ERROR]      {e}")
    print(f"::error::{e}")
print("\n=== Resumen de correcciones ===")
for a in applied:
    print(f"  [OK]      {a}")
for s in skipped:
    print(f"  [=]       {s}")
for m in missing:
    print(f"  [OMITIDO] {m}")
    print(f"::warning::{m}")

if diag:
    print("\n=== Diagnostico: como se ve ese codigo en tu rama (para adaptar los parches omitidos) ===")
    for d in diag:
        print(d)
        print()

if restore_errors:
    print("\nNo se pudieron restaurar algunos archivos del paquete anterior. "
          "Restauralos a mano desde el historial de tu fork y vuelve a ejecutar.")
    sys.exit(1)
