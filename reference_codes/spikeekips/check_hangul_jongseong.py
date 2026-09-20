import logging

log = logging.getLogger('check-hangul-jongseong')
log.setLevel(logging.ERROR)


def check_HANGUL_JONGSEONG(w):
    '''
    >>> check_HANGUL_JONGSEONG('우리')
    False
    >>> check_HANGUL_JONGSEONG('사람')
    True
    >>> check_HANGUL_JONGSEONG('하')
    False
    >>> check_HANGUL_JONGSEONG('까닭')
    True
    >>> check_HANGUL_JONGSEONG('\u110b\u116e\u1105\u1175') # `우리`(NFD)
    False
    >>> check_HANGUL_JONGSEONG('\u1101\u1161\u1103\u1161\u11b0') # `까닭`(NFD)
    True
    '''
    from unicodedata import name, normalize 
    
    log.debug('> %s', w)
    log.debug('{:>30}: %s'.format('unicode'), w.encode('unicode-escape').decode('utf-8'))
    log.debug('{:>30}: %s'.format('unicode(normalized to NFD'), normalize('NFD', w).encode('unicode-escape').decode('utf-8'))
    
    has_JONGSEONG = 'JONGSEONG' in name(normalize('NFD', w[-1])[-1])
    
    log.debug('{:>30}: %s'.format('has HANGUL JONGSEONG'), has_JONGSEONG)
    
    return has_JONGSEONG