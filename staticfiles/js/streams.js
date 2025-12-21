/**
 * Video Chat Client - Updated with Latest Agora SDK Patterns
 *
 * Features:
 * - Latest Agora RTC SDK patterns
 * - Autoplay failure handling
 * - Device hot-plug support
 * - CSRF protection for API calls
 * - Input validation
 * - Comprehensive error handling
 * - Secure session management
 */

// Enable Agora log upload for debugging
AgoraRTC.enableLogUpload();

// Get configuration from sessionStorage (populated by backend)
const APP_ID = sessionStorage.getItem('appId')
const TOKEN = sessionStorage.getItem('token')
const CHANNEL = sessionStorage.getItem('room')
let UID = sessionStorage.getItem('UID')
let NAME = sessionStorage.getItem('name')

// Validate required configuration
if (!APP_ID || !TOKEN || !CHANNEL || !UID || !NAME) {
    console.error('Missing required configuration. Redirecting to lobby...')
    window.location.href = '/'
}

// Initialize Agora client
const client = AgoraRTC.createClient({ mode: 'rtc', codec: 'vp8' })

// State management
let localTracks = {
    videoTrack: null,
    audioTrack: null
}
let remoteUsers = {}
let connectedUsers = {} // Track user names: { uid: name }
let isJoined = false

// ==============================================================================
// AGORA EVENT HANDLERS
// ==============================================================================

/**
 * Handle autoplay failure (browser policy)
 */
AgoraRTC.onAutoplayFailed = () => {
    console.warn('Autoplay failed - user interaction required')
    const btn = document.createElement('button')
    btn.innerText = 'Click to Enable Audio/Video'
    btn.style.cssText = 'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:9999;padding:20px 40px;font-size:18px;cursor:pointer;background:#4CAF50;color:white;border:none;border-radius:8px;'
    btn.onclick = () => {
        btn.remove()
        // Resume audio context
        if (localTracks.audioTrack) {
            localTracks.audioTrack.play()
        }
        // Resume remote audio tracks
        Object.values(remoteUsers).forEach(user => {
            if (user.audioTrack) {
                user.audioTrack.play()
            }
        })
    }
    document.body.appendChild(btn)
}

/**
 * Handle microphone hot-plug
 */
AgoraRTC.onMicrophoneChanged = async (changedDevice) => {
    if (!localTracks.audioTrack) return

    try {
        if (changedDevice.state === 'ACTIVE') {
            // New microphone plugged in - switch to it
            console.log('New microphone detected:', changedDevice.device.label)
            await localTracks.audioTrack.setDevice(changedDevice.device.deviceId)
        } else if (changedDevice.device.label === localTracks.audioTrack.getTrackLabel()) {
            // Current microphone unplugged - switch to another
            const mics = await AgoraRTC.getMicrophones()
            if (mics[0]) {
                console.log('Switching to microphone:', mics[0].label)
                await localTracks.audioTrack.setDevice(mics[0].deviceId)
            }
        }
    } catch (error) {
        console.error('Error handling microphone change:', error)
    }
}

/**
 * Handle camera hot-plug
 */
AgoraRTC.onCameraChanged = async (changedDevice) => {
    if (!localTracks.videoTrack) return

    try {
        if (changedDevice.state === 'ACTIVE') {
            // New camera plugged in - switch to it
            console.log('New camera detected:', changedDevice.device.label)
            await localTracks.videoTrack.setDevice(changedDevice.device.deviceId)
        } else if (changedDevice.device.label === localTracks.videoTrack.getTrackLabel()) {
            // Current camera unplugged - switch to another
            const cams = await AgoraRTC.getCameras()
            if (cams[0]) {
                console.log('Switching to camera:', cams[0].label)
                await localTracks.videoTrack.setDevice(cams[0].deviceId)
            }
        }
    } catch (error) {
        console.error('Error handling camera change:', error)
    }
}

// ==============================================================================
// CSRF TOKEN MANAGEMENT
// ==============================================================================

/**
 * Get CSRF token from cookie
 */
function getCookie(name) {
    let cookieValue = null
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';')
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim()
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1))
                break
            }
        }
    }
    return cookieValue
}

const csrftoken = getCookie('csrftoken')

// ==============================================================================
// UTILITY FUNCTIONS
// ==============================================================================

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    }
    return String(text).replace(/[&<>"']/g, m => map[m])
}

/**
 * Update the connected users display
 */
function updateConnectedUsersDisplay() {
    const userCountEl = document.getElementById('user-count')
    const connectedUsersEl = document.getElementById('connected-users')

    if (!userCountEl || !connectedUsersEl) return

    // Log current state for debugging
    console.log('Connected users:', JSON.stringify(connectedUsers))

    const userNames = Object.values(connectedUsers)
    userCountEl.innerText = userNames.length

    // Build the user list HTML - show names, not UIDs
    let html = ''
    userNames.forEach(name => {
        // Ensure we're displaying a valid name string
        const displayName = (typeof name === 'string' && name.length > 0) ? name : 'Unknown'
        html += `<span class="connected-user">${escapeHtml(displayName)}</span>`
    })
    connectedUsersEl.innerHTML = html
}

// ==============================================================================
// MAIN FUNCTIONS
// ==============================================================================

/**
 * Join Agora channel and display local video stream
 */
let joinAndDisplayLocalStream = async () => {
    try {
        // Display room name
        document.getElementById('room-name').innerText = CHANNEL

        // Set up client event handlers BEFORE joining
        client.on('user-published', handleUserPublished)
        client.on('user-unpublished', handleUserUnpublished)
        client.on('user-left', handleUserLeft)

        // Join channel with token
        try {
            UID = await client.join(APP_ID, CHANNEL, TOKEN, parseInt(UID))
            isJoined = true
            console.log('Successfully joined channel:', CHANNEL, 'with UID:', UID)
        } catch (error) {
            console.error('Failed to join channel:', error)
            if (error.code === 'CAN_NOT_GET_GATEWAY_SERVER') {
                alert('Token error. Please try again.')
            } else {
                alert('Failed to join video room: ' + error.message)
            }
            window.location.href = '/'
            return
        }

        // Create local audio and video tracks
        try {
            const [audioTrack, videoTrack] = await Promise.all([
                AgoraRTC.createMicrophoneAudioTrack({
                    encoderConfig: 'music_standard'
                }),
                AgoraRTC.createCameraVideoTrack()
            ])
            localTracks.audioTrack = audioTrack
            localTracks.videoTrack = videoTrack
        } catch (error) {
            console.error('Failed to get media devices:', error)
            alert('Please allow microphone and camera access to join the video chat.')
            await client.leave()
            window.location.href = '/'
            return
        }

        // Create member in database
        let member = await createMember()
        if (!member) {
            console.error('Failed to create member record')
            await cleanup()
            return
        }

        // Add local user to connected users list
        connectedUsers[UID] = member.name
        console.log('Local user added:', UID, '=', member.name)
        updateConnectedUsersDisplay()

        // Create and display local video player
        let player = `<div class="video-container" id="user-container-${UID}">
                         <div class="video-player" id="user-${UID}"></div>
                         <div class="username-wrapper"><span class="user-name">${escapeHtml(member.name)}</span></div>
                      </div>`

        document.getElementById('video-streams').insertAdjacentHTML('beforeend', player)

        // Play local video
        localTracks.videoTrack.play(`user-${UID}`)

        // Publish local tracks to channel
        await client.publish([localTracks.audioTrack, localTracks.videoTrack])
        console.log('Local stream published successfully')

    } catch (error) {
        console.error('Error in joinAndDisplayLocalStream:', error)
        alert('An error occurred while joining the room.')
        await cleanup()
    }
}

/**
 * Handle remote user publishing media (user-published event)
 */
async function handleUserPublished(user, mediaType) {
    const uid = user.uid
    console.log('User published:', uid, 'mediaType:', mediaType)

    // Store remote user reference
    remoteUsers[uid] = user

    // Subscribe to the remote user's track
    try {
        await client.subscribe(user, mediaType)
        console.log('Subscribed to user:', uid, 'mediaType:', mediaType)
    } catch (error) {
        console.error('Failed to subscribe to user:', uid, error)
        return
    }

    // Get member info if we don't have it yet (on first media type published)
    if (!connectedUsers[uid]) {
        let member = await getMember(user)
        connectedUsers[uid] = member.name
        console.log('Added user to connected list:', uid, '=', member.name)
        updateConnectedUsersDisplay()
    }

    if (mediaType === 'video') {
        // Remove existing player if any
        let playerContainer = document.getElementById(`user-container-${uid}`)
        if (playerContainer) {
            playerContainer.remove()
        }

        // Get member name from our cache
        const memberName = connectedUsers[uid] || 'Unknown User'

        // Create player element
        let player = `<div class="video-container" id="user-container-${uid}">
                <div class="video-player" id="user-${uid}"></div>
                <div class="username-wrapper"><span class="user-name">${escapeHtml(memberName)}</span></div>
            </div>`

        document.getElementById('video-streams').insertAdjacentHTML('beforeend', player)

        // Play remote video
        user.videoTrack.play(`user-${uid}`)
        console.log('Remote video displayed for user:', uid)
    }

    if (mediaType === 'audio') {
        // Play remote audio
        user.audioTrack.play()
        console.log('Remote audio playing for user:', uid)
    }
}

/**
 * Handle remote user unpublishing media (user-unpublished event)
 */
function handleUserUnpublished(user, mediaType) {
    const uid = user.uid
    console.log('User unpublished:', uid, 'mediaType:', mediaType)

    if (mediaType === 'video') {
        // Remove video player when user stops publishing video
        const playerContainer = document.getElementById(`user-container-${uid}`)
        if (playerContainer) {
            playerContainer.remove()
        }
    }
}

/**
 * Handle remote user leaving
 */
function handleUserLeft(user) {
    const uid = user.uid
    console.log('User left:', uid)

    delete remoteUsers[uid]
    delete connectedUsers[uid]
    updateConnectedUsersDisplay()

    const container = document.getElementById(`user-container-${uid}`)
    if (container) {
        container.remove()
    }
}

/**
 * Leave channel and cleanup
 */
let leaveAndRemoveLocalStream = async () => {
    await cleanup()
    window.location.href = '/'
}

/**
 * Cleanup function for leaving channel
 */
async function cleanup() {
    try {
        // Stop and close local tracks
        if (localTracks.audioTrack) {
            localTracks.audioTrack.stop()
            localTracks.audioTrack.close()
            localTracks.audioTrack = null
        }
        if (localTracks.videoTrack) {
            localTracks.videoTrack.stop()
            localTracks.videoTrack.close()
            localTracks.videoTrack = null
        }

        // Leave channel if joined
        if (isJoined) {
            await client.leave()
            isJoined = false
            console.log('Left channel successfully')
        }

        // Delete member from database
        await deleteMember()

        // Clear remote users and connected users
        remoteUsers = {}
        connectedUsers = {}

        // Clear session storage
        sessionStorage.removeItem('UID')
        sessionStorage.removeItem('token')
        sessionStorage.removeItem('room')
        sessionStorage.removeItem('name')
        sessionStorage.removeItem('appId')

    } catch (error) {
        console.error('Error during cleanup:', error)
    }
}

/**
 * Toggle camera on/off
 */
let toggleCamera = async (e) => {
    try {
        if (!localTracks.videoTrack) return

        if (localTracks.videoTrack.muted) {
            await localTracks.videoTrack.setMuted(false)
            e.target.style.backgroundColor = '#fff'
            console.log('Camera enabled')
        } else {
            await localTracks.videoTrack.setMuted(true)
            e.target.style.backgroundColor = 'rgb(255, 80, 80, 1)'
            console.log('Camera disabled')
        }
    } catch (error) {
        console.error('Error toggling camera:', error)
    }
}

/**
 * Toggle microphone on/off
 */
let toggleMic = async (e) => {
    try {
        if (!localTracks.audioTrack) return

        if (localTracks.audioTrack.muted) {
            await localTracks.audioTrack.setMuted(false)
            e.target.style.backgroundColor = '#fff'
            console.log('Microphone enabled')
        } else {
            await localTracks.audioTrack.setMuted(true)
            e.target.style.backgroundColor = 'rgb(255, 80, 80, 1)'
            console.log('Microphone disabled')
        }
    } catch (error) {
        console.error('Error toggling microphone:', error)
    }
}

// ==============================================================================
// API FUNCTIONS
// ==============================================================================

/**
 * Create member in database (with CSRF protection)
 */
let createMember = async () => {
    try {
        let response = await fetch('/create_member/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrftoken
            },
            credentials: 'same-origin',
            body: JSON.stringify({
                'name': NAME,
                'room_name': CHANNEL,
                'UID': UID
            })
        })

        if (!response.ok) {
            let error = await response.json()
            throw new Error(error.error || 'Failed to create member')
        }

        let member = await response.json()
        console.log('Member created:', member)
        return member
    } catch (error) {
        console.error('Error creating member:', error)
        alert('Failed to join room: ' + error.message)
        return null
    }
}

/**
 * Get member info from database with retry logic
 */
let getMember = async (user, retries = 5) => {
    for (let attempt = 1; attempt <= retries; attempt++) {
        try {
            let response = await fetch(
                `/get_member/?UID=${encodeURIComponent(user.uid)}&room_name=${encodeURIComponent(CHANNEL)}`,
                {
                    credentials: 'same-origin'
                }
            )

            if (!response.ok) {
                let error = await response.json()
                throw new Error(error.error || 'Failed to get member')
            }

            let member = await response.json()

            // Check if member has valid name
            if (member && member.name) {
                return member
            }

            // If no valid member, wait and retry
            if (attempt < retries) {
                console.log(`Member not found for UID ${user.uid}, retrying (${attempt}/${retries})...`)
                await new Promise(resolve => setTimeout(resolve, 500 * attempt))
            }
        } catch (error) {
            console.error(`Error getting member (attempt ${attempt}/${retries}):`, error)
            if (attempt < retries) {
                await new Promise(resolve => setTimeout(resolve, 500 * attempt))
            }
        }
    }

    console.error('Failed to get member after all retries for UID:', user.uid)
    return { name: 'Unknown User' }  // Fallback instead of null
}

/**
 * Delete member from database (with CSRF protection)
 */
let deleteMember = async () => {
    try {
        let response = await fetch('/delete_member/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrftoken
            },
            credentials: 'same-origin',
            body: JSON.stringify({
                'name': NAME,
                'room_name': CHANNEL,
                'UID': UID
            })
        })

        if (!response.ok) {
            let error = await response.json()
            console.warn('Failed to delete member:', error.error)
        } else {
            console.log('Member deleted successfully')
        }
    } catch (error) {
        console.error('Error deleting member:', error)
    }
}

// ==============================================================================
// EVENT LISTENERS
// ==============================================================================

// Cleanup when user closes tab/window
window.addEventListener('beforeunload', deleteMember)

// Start the application
joinAndDisplayLocalStream()

// Set up control button listeners
document.getElementById('leave-btn').addEventListener('click', leaveAndRemoveLocalStream)
document.getElementById('camera-btn').addEventListener('click', toggleCamera)
document.getElementById('mic-btn').addEventListener('click', toggleMic)
