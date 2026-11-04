/*
 * zh-gettext：给 Termux 原生 XFCE 补上界面翻译。
 *
 * Termux 打包时删掉了 share/locale，GLib 的 g_dgettext 也是空实现（没有链接 libintl），
 * 所以 LANG=zh_CN 时 XFCE/GTK 仍是英文。本库通过 LD_PRELOAD 接管 GLib 导出的
 * g_dgettext / g_dcgettext / g_dngettext / g_dpgettext / g_dpgettext2，
 * 从 $ZH_GETTEXT_DIR（默认 ~/.local/share/locale）/<语言>/LC_MESSAGES/<domain>.mo 查翻译。
 * 找不到语言、文件或词条时原样返回英文，不影响程序运行。
 *
 * 编译（手机 Termux）：clang -shared -fPIC -O2 -o libzh-gettext.so zh-gettext.c
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#define MO_MAGIC 0x950412deu
#define MO_MAGIC_SWAPPED 0xde120495u

struct catalog {
    char *domain;
    const unsigned char *data;  /* NULL 表示该 domain 没有翻译文件 */
    size_t size;
    uint32_t count, orig_off, trans_off;
    int swapped;
    struct catalog *next;
};

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static struct catalog *catalogs;
static char langs[4][32];
static int nlangs = -1;
static char base_dir[512];
static char default_domain[64];

/* 没指明翻译域（domain 为 NULL）时用的域：xfce_textdomain() 设置的包名，否则用程序名。 */
static const char *resolve_domain(const char *domain)
{
    if (domain)
        return domain;
    if (default_domain[0])
        return default_domain;
    return getprogname();
}

/* XFCE 程序启动时调用 xfce_textdomain(GETTEXT_PACKAGE, ...)；记下包名后交给原函数。 */
void xfce_textdomain(const char *package, const char *localedir, const char *encoding)
{
    if (package)
        strncpy(default_domain, package, sizeof default_domain - 1);
    void (*real)(const char *, const char *, const char *) =
        (void (*)(const char *, const char *, const char *))dlsym(RTLD_NEXT, "xfce_textdomain");
    if (real)
        real(package, localedir, encoding);
}

static uint32_t rd32(const struct catalog *c, size_t off)
{
    uint32_t v;
    if (off + 4 > c->size)
        return 0;
    memcpy(&v, c->data + off, 4);
    return c->swapped ? __builtin_bswap32(v) : v;
}

static void add_lang(const char *s, size_t n)
{
    if (n == 0 || n >= sizeof langs[0] || nlangs >= 4)
        return;
    for (int i = 0; i < nlangs; i++)
        if (strlen(langs[i]) == n && strncmp(langs[i], s, n) == 0)
            return;
    memcpy(langs[nlangs], s, n);
    langs[nlangs][n] = 0;
    nlangs++;
}

/* zh_CN.UTF-8@x → zh_CN、zh */
static void add_locale_name(const char *name, size_t n)
{
    size_t cut = n;
    for (size_t i = 0; i < n; i++)
        if (name[i] == '.' || name[i] == '@') {
            cut = i;
            break;
        }
    if (cut == 0 || (cut == 1 && name[0] == 'C') || (cut == 5 && strncmp(name, "POSIX", 5) == 0))
        return;
    add_lang(name, cut);
    for (size_t i = 0; i < cut; i++)
        if (name[i] == '_') {
            add_lang(name, i);
            break;
        }
}

static void init_langs(void)
{
    nlangs = 0;
    const char *lang = NULL;
    const char *vars[] = {"LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"};
    for (int i = 0; i < 4 && !lang; i++) {
        const char *v = getenv(vars[i]);
        if (v && *v)
            lang = v;
    }
    if (lang) {
        const char *p = lang;
        while (*p) {
            const char *e = strchr(p, ':');
            size_t n = e ? (size_t)(e - p) : strlen(p);
            add_locale_name(p, n);
            p += n + (e ? 1 : 0);
        }
    }
    const char *dir = getenv("ZH_GETTEXT_DIR");
    const char *home = getenv("HOME");
    if (dir && *dir) {
        strncpy(base_dir, dir, sizeof base_dir - 1);
    } else if (home) {
        strncpy(base_dir, home, sizeof base_dir - 32);
        strcat(base_dir, "/.local/share/locale");
    }
}

static struct catalog *load(const char *domain)
{
    struct catalog *c = calloc(1, sizeof *c);
    if (!c)
        return NULL;
    c->domain = strdup(domain);
    for (int i = 0; i < nlangs && !c->data && base_dir[0]; i++) {
        char path[768];
        size_t need = strlen(base_dir) + strlen(langs[i]) + strlen(domain) + 20;
        if (need >= sizeof path)
            continue;
        strcpy(path, base_dir);
        strcat(path, "/");
        strcat(path, langs[i]);
        strcat(path, "/LC_MESSAGES/");
        strcat(path, domain);
        strcat(path, ".mo");
        int fd = open(path, O_RDONLY | O_CLOEXEC);
        if (fd < 0)
            continue;
        struct stat st;
        if (fstat(fd, &st) == 0 && st.st_size >= 28) {
            void *m = mmap(NULL, (size_t)st.st_size, PROT_READ, MAP_PRIVATE, fd, 0);
            if (m != MAP_FAILED) {
                c->data = m;
                c->size = (size_t)st.st_size;
            }
        }
        close(fd);
        if (!c->data)
            continue;
        uint32_t magic;
        memcpy(&magic, c->data, 4);
        if (magic == MO_MAGIC_SWAPPED)
            c->swapped = 1;
        else if (magic != MO_MAGIC) {
            munmap((void *)c->data, c->size);
            c->data = NULL;
            continue;
        }
        c->count = rd32(c, 8);
        c->orig_off = rd32(c, 12);
        c->trans_off = rd32(c, 16);
        if ((uint64_t)c->orig_off + 8ull * c->count > c->size || (uint64_t)c->trans_off + 8ull * c->count > c->size) {
            munmap((void *)c->data, c->size);
            c->data = NULL;
        }
    }
    c->next = catalogs;
    catalogs = c;
    return c;
}

/* 返回译文（含复数时为以 \0 分隔的多段）及其长度；没有则 NULL。 */
static const char *find(const char *domain, const char *key, size_t *len)
{
    domain = resolve_domain(domain);
    if (!domain || !key)
        return NULL;
    pthread_mutex_lock(&lock);
    if (nlangs < 0)
        init_langs();
    struct catalog *c = NULL;
    if (nlangs > 0) {
        for (c = catalogs; c; c = c->next)
            if (strcmp(c->domain, domain) == 0)
                break;
        if (!c)
            c = load(domain);
    }
    pthread_mutex_unlock(&lock);
    if (!c || !c->data)
        return NULL;
    uint32_t lo = 0, hi = c->count;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        uint32_t olen = rd32(c, c->orig_off + 8 * mid);
        uint32_t ooff = rd32(c, c->orig_off + 8 * mid + 4);
        if ((uint64_t)ooff + olen >= c->size)
            return NULL;
        int cmp = strcmp(key, (const char *)c->data + ooff);
        if (cmp == 0) {
            uint32_t tlen = rd32(c, c->trans_off + 8 * mid);
            uint32_t toff = rd32(c, c->trans_off + 8 * mid + 4);
            if (tlen == 0 || (uint64_t)toff + tlen >= c->size)
                return NULL;
            *len = tlen;
            return (const char *)c->data + toff;
        }
        if (cmp < 0)
            hi = mid;
        else
            lo = mid + 1;
    }
    return NULL;
}

const char *g_dgettext(const char *domain, const char *msgid)
{
    size_t len;
    const char *t = find(domain, msgid, &len);
    return t ? t : msgid;
}

const char *g_dcgettext(const char *domain, const char *msgid, int category)
{
    (void)category;
    return g_dgettext(domain, msgid);
}

const char *g_dngettext(const char *domain, const char *msgid, const char *msgid_plural, unsigned long n)
{
    size_t len;
    const char *t = find(domain, msgid, &len);
    if (!t)
        return n == 1 ? msgid : msgid_plural;
    /* 中文只有一种形式；其它语言按“1 用第一种，其余用第二种（若有）”近似。 */
    if (n != 1) {
        size_t first = strlen(t);
        if (first + 1 < len)
            return t + first + 1;
    }
    return t;
}

const char *g_dpgettext(const char *domain, const char *msgctxtid, size_t msgidoffset)
{
    size_t len;
    const char *t = find(domain, msgctxtid, &len);
    if (t)
        return t;
    if (msgidoffset > 0)
        return msgctxtid + msgidoffset;
    const char *sep = strchr(msgctxtid, '|');
    if (!sep)
        return msgctxtid;
    size_t n = strlen(msgctxtid);
    char *tmp = malloc(n + 1);
    if (tmp) {
        memcpy(tmp, msgctxtid, n + 1);
        tmp[sep - msgctxtid] = '\004';
        t = find(domain, tmp, &len);
        free(tmp);
        if (t)
            return t;
    }
    return sep + 1;
}

const char *g_dpgettext2(const char *domain, const char *context, const char *msgid)
{
    size_t cl = strlen(context), ml = strlen(msgid), len;
    char stackbuf[512];
    char *key = cl + ml + 2 <= sizeof stackbuf ? stackbuf : malloc(cl + ml + 2);
    const char *t = NULL;
    if (key) {
        memcpy(key, context, cl);
        key[cl] = '\004';
        memcpy(key + cl + 1, msgid, ml + 1);
        t = find(domain, key, &len);
        if (key != stackbuf)
            free(key);
    }
    return t ? t : msgid;
}
