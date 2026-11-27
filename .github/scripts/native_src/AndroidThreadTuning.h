// Added by apply_fixes.py: scheduling hints for big.LITTLE phones (e.g. Moto G20 / Unisoc T700).
//
// Emulation needs two fast threads (emulated CPU + GPU command processing). Left alone, Android's
// scheduler often runs them on the slow LITTLE cores, or lets background work steal the big ones,
// which shows up as random FPS drops. This pins those two threads to the big cores and raises their
// priority, while helper threads (shader compilation) stay free to use any core.
#pragma once

#if defined(__ANDROID__)

#include <cstdio>
#include <vector>

#include <sched.h>
#include <sys/resource.h>
#include <unistd.h>

namespace Common::AndroidTuning
{
struct CoreSets
{
  std::vector<int> big;
  std::vector<int> little;
  int total = 0;
};

inline bool IsLittleCorePart(unsigned part)
{
  switch (part)
  {
  case 0xd03:  // Cortex-A53
  case 0xd04:  // Cortex-A35
  case 0xd05:  // Cortex-A55
  case 0xd46:  // Cortex-A510
  case 0xd80:  // Cortex-A520
    return true;
  default:
    return false;
  }
}

inline CoreSets DetectCores()
{
  CoreSets sets;
  const long n = sysconf(_SC_NPROCESSORS_CONF);
  if (n <= 1 || n > 64)
    return sets;
  sets.total = static_cast<int>(n);

  // 1) Kernel-reported capacity (preferred): big cores ~1024, little cores much lower.
  std::vector<long> capacity(static_cast<size_t>(n), -1);
  bool have_capacity = true;
  long max_capacity = 0;
  for (long i = 0; i < n && have_capacity; ++i)
  {
    char path[96];
    std::snprintf(path, sizeof(path), "/sys/devices/system/cpu/cpu%ld/cpu_capacity", i);
    FILE* f = std::fopen(path, "r");
    if (!f)
    {
      have_capacity = false;
      break;
    }
    long value = -1;
    if (std::fscanf(f, "%ld", &value) != 1)
      have_capacity = false;
    std::fclose(f);
    capacity[static_cast<size_t>(i)] = value;
    if (value > max_capacity)
      max_capacity = value;
  }

  if (have_capacity && max_capacity > 0)
  {
    for (long i = 0; i < n; ++i)
    {
      if (capacity[static_cast<size_t>(i)] * 100 >= max_capacity * 70)
        sets.big.push_back(static_cast<int>(i));
      else
        sets.little.push_back(static_cast<int>(i));
    }
  }
  else
  {
    // 2) Fallback: ARM "CPU part" ids from /proc/cpuinfo.
    std::vector<unsigned> parts;
    if (FILE* f = std::fopen("/proc/cpuinfo", "r"))
    {
      char line[256];
      while (std::fgets(line, sizeof(line), f))
      {
        unsigned part = 0;
        if (std::sscanf(line, "CPU part : %x", &part) == 1)
          parts.push_back(part);
      }
      std::fclose(f);
    }
    if (static_cast<long>(parts.size()) == n)
    {
      for (long i = 0; i < n; ++i)
      {
        if (IsLittleCorePart(parts[static_cast<size_t>(i)]))
          sets.little.push_back(static_cast<int>(i));
        else
          sets.big.push_back(static_cast<int>(i));
      }
    }
  }

  // Homogeneous CPU, or a single big core: pinning would not help.
  if (sets.big.size() < 2 || sets.little.empty())
  {
    sets.big.clear();
    sets.little.clear();
  }
  return sets;
}

inline const CoreSets& Cores()
{
  static const CoreSets sets = DetectCores();
  return sets;
}

inline void SetAffinity(const std::vector<int>& cpus)
{
  if (cpus.empty())
    return;
  cpu_set_t set;
  CPU_ZERO(&set);
  for (const int cpu : cpus)
    CPU_SET(cpu, &set);
  sched_setaffinity(0, sizeof(set), &set);  // pid 0 == calling thread; failure is harmless
}

// For the two heavy emulation threads (emulated CPU, GPU command processor).
inline void BoostEmuThread()
{
  SetAffinity(Cores().big);
  // Android lets apps lower their own threads' nice value; failure is harmless.
  setpriority(PRIO_PROCESS, 0, -8);
}

// For helper threads that may be created by an already-pinned thread (they would inherit its CPU
// mask and compete with the emulation threads for the big cores).
inline void BackgroundWorker()
{
  const CoreSets& sets = Cores();
  if (sets.total <= 0)
    return;
  std::vector<int> all;
  for (int i = 0; i < sets.total; ++i)
    all.push_back(i);
  SetAffinity(all);
}
}  // namespace Common::AndroidTuning

#else

namespace Common::AndroidTuning
{
inline void BoostEmuThread()
{
}
inline void BackgroundWorker()
{
}
}  // namespace Common::AndroidTuning

#endif
