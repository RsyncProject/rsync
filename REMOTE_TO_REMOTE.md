# Remote-to-Remote Transfer Feature

## Overview

This feature adds support for direct remote-to-remote transfers in rsync, eliminating the long-standing limitation where rsync would error with "The source and destination cannot both be remote."

## Usage

```bash
rsync --remote-to-remote [options] source_host:/path dest_host:/path
```

## Examples

### Basic remote-to-remote transfer
```bash
rsync --remote-to-remote -avz server1:/data/ server2:/backup/
```

### With compression and progress
```bash
rsync --remote-to-remote -avz --progress server1:/home/user/ server2:/home/user/
```

### Update only newer files
```bash
rsync --remote-to-remote --update -avz web1:/var/www/ web2:/var/www/
```

## How It Works

When the `--remote-to-remote` option is specified and both source and destination are remote:

1. **SSH Connection**: rsync establishes an SSH connection to the source host
2. **Remote Execution**: Executes rsync on the source host with all original options
3. **Direct Transfer**: The source host connects directly to the destination host
4. **Efficient Sync**: Files are transferred directly between remote hosts without intermediate local storage

## Technical Details

### Command Translation
Original command:
```bash
rsync --remote-to-remote -avz host1:/src host2:/dst
```

Gets translated to:
```bash
ssh host1 rsync -avz /src host2:/dst
```

### Compatibility
- Maintains full compatibility with all existing rsync options
- Preserves rsync's delta-sync algorithm efficiency  
- Works with SSH keys, config files, and authentication methods
- Supports all rsync transfer modes (archive, compression, etc.)

### Requirements
- SSH access to the source host
- The source host must be able to connect to the destination host
- rsync must be installed on the source host

## Error Handling

The feature includes comprehensive error handling:
- Validates command line arguments
- Provides clear error messages for configuration issues
- Falls back gracefully when SSH connections fail
- Maintains original rsync error reporting

## Implementation

The implementation adds:
- New command line option `--remote-to-remote`
- Enhanced argument parsing in `options.c`
- New remote-to-remote transfer function in `main.c`  
- Proper integration with existing rsync architecture

## Testing

The feature has been tested with:
- ✅ Various rsync options (-avz, --update, --progress, etc.)
- ✅ Different path formats and hostnames
- ✅ Error conditions and edge cases
- ✅ Integration with existing SSH configurations

## Benefits

1. **No Intermediate Storage**: Files transfer directly between remote hosts
2. **Full rsync Efficiency**: Maintains delta-sync and all rsync optimizations  
3. **Network Efficiency**: Reduces bandwidth usage compared to two-step transfers
4. **Simplified Workflows**: Eliminates need for complex scripting workarounds
5. **Backward Compatible**: Doesn't break existing rsync functionality

## Future Enhancements

Potential improvements for future versions:
- Automatic host resolution optimization
- Enhanced error reporting for network issues  
- Support for daemon-mode remote-to-remote transfers
- Connection pooling for multiple transfers